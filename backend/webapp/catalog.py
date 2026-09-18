"""
Catalogue harvester: finds public-domain and openly licensed titles for every shelf and adds them to the library as
catalogue entries (the original's address and credits only; the builder writes the summary later).

  movies    Internet Archive feature, silent, noir, sci-fi and comedy films published by 1965 (public domain in India),
            marked public domain or with a Creative Commons licence that allows adaptations and commercial use
  series    Internet Archive classic TV episodes on the same terms, grouped by show
  books     Project Gutenberg non-fiction whose every author and translator died by 1965
  stories   Project Gutenberg fiction, short stories, fables and plays on the same terms
  lectures  YouTube uploads marked Creative Commons (the licence is checked again before a summary is made)
harvest() takes everything the sources offer, or tops each shelf up to a number. Another upload of a film already on
the shelf, or another edition of the same book, is listed once.
Nothing is copied: entries point at the original, and the builder re-checks the rights before summarising.
"""
import csv
import gzip
import io
import json
import re
import subprocess
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, quote_plus

import httpx

from backend.config import TEMP_DIR
from backend.video_engine import rights
from backend.webapp import library as lib

UA = rights.UA
ARCHIVE = "https://archive.org/advancedsearch.php"
FILM_COLLECTIONS = "(feature_films OR silent_films OR film_noir OR sci-fi_horror OR comedy_films)"
LICENSED = "(licenseurl:*publicdomain* OR licenseurl:*creativecommons*)"
GUTENBERG_CSV = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz"
FICTION_RE = re.compile(r"\bfiction\b|short stories|fairy tales|fables|folklore|legends|\bnovels?\b|adventure stories|detective and mystery"
                        r"|ghost stories|romances|\bdrama\b|\bplays\b|children's stories|humorous stories|love stories", re.I)
NONFICTION_LOCC = tuple("BDEFGHJKLQRSTUZ")
LECTURE_TOPICS = [
    "calculus", "linear algebra", "differential equations", "real analysis", "number theory", "discrete mathematics",
    "probability", "statistics", "physics", "quantum mechanics", "classical mechanics", "electromagnetism", "thermodynamics",
    "optics", "solid state physics", "nuclear physics", "astrophysics", "chemistry", "organic chemistry", "inorganic chemistry",
    "physical chemistry", "biochemistry", "biology", "cell biology", "molecular biology", "genetics", "microbiology", "ecology",
    "evolution", "neuroscience", "anatomy", "physiology", "pharmacology", "medicine", "public health", "nutrition", "economics",
    "microeconomics", "macroeconomics", "econometrics", "finance", "accounting", "marketing", "management", "operations research",
    "supply chain", "entrepreneurship", "psychology", "sociology", "anthropology", "political science", "international relations",
    "philosophy", "ethics", "logic", "world history", "indian history", "ancient india", "medieval history", "art history",
    "architecture", "music theory", "literature", "english grammar", "linguistics", "sanskrit", "computer science", "programming",
    "python programming", "java programming", "data structures", "algorithms", "machine learning", "deep learning",
    "artificial intelligence", "data science", "databases", "operating systems", "computer networks", "cyber security", "compilers",
    "computer architecture", "digital electronics", "signal processing", "control systems", "electrical engineering", "electronics",
    "mechanical engineering", "fluid mechanics", "strength of materials", "civil engineering", "chemical engineering",
    "aerospace engineering", "materials science", "manufacturing", "robotics", "astronomy", "geology", "climate science",
    "environmental science", "geography", "law", "constitution of india", "education", "agriculture",
]
HINDI_LECTURE_TOPICS = [
    "physics in hindi", "chemistry in hindi", "maths in hindi", "biology in hindi", "history in hindi", "economics in hindi",
    "geography in hindi", "polity in hindi", "accounting in hindi", "computer science in hindi", "programming in hindi",
    "electrical engineering in hindi", "mechanical engineering in hindi", "civil engineering in hindi", "psychology in hindi",
    "sociology in hindi", "philosophy in hindi", "hindi literature", "sanskrit in hindi", "class 12 in hindi", "class 10 in hindi",
    "science in hindi", "commerce in hindi",
]
NOT_LECTURE_RE = re.compile(r"full movie|\bsongs?\b|trailer|gameplay|\basmr\b|\bvlog|prank|reaction|live ?stream|\bpodcast|audiobook"
                            r"|meditation|workout|bhajan|kirtan|\bnews\b|\bmeme", re.I)
LECTURE_WORDS_RE = re.compile(r"lecture|\blec\b|lesson|\bclass\b|course|seminar|\btalk\b|tutorial|chapter|\bunit\b|module|\bweek\b"
                              r"|introduction|explained|nptel|\bmod\b|part\s*\d|session|webinar|workshop|\bपाठ|अध्याय|व्याख्यान", re.I)
CHANNEL_CAP = 1000                     # at most this many lectures from one channel, so one uploader cannot fill the shelf
STATE: Dict[str, Any] = {"running": False, "message": "", "counts": {}, "started": None, "finished": None}
_lock = threading.Lock()


def _clean(text: Any) -> str:
    if isinstance(text, list):
        text = text[0] if text else ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


MATURE_RE = re.compile(r"\bsex\b|\bsexual|nudist|\bnude\b|strip-?tease|erotic|white slave|stag film", re.I)   # not "burlesque": Chaplin's are parodies


def _title_key(kind: str, title: str, extra: Any = "") -> str:
    """One key per work: case, punctuation, '(1940)' and '(2nd edition)' are ignored; `extra` is the year or the author."""
    t = re.sub(r"\([^)]*(?:\d{4}|edition|version|ed\.)[^)]*\)", " ", (title or "").lower())
    t = re.sub(r"[^\wऀ-෿]+|_", " ", t).strip()          # keeps Indic vowel signs, which \w leaves out
    x = re.sub(r"[^\wऀ-෿]+|_", " ", str(extra or "").lower()).strip()
    return f"title:{kind}:{t}:{x}"


def _existing_urls() -> set:
    """Addresses already in the library, 'archive:<identifier>' so a film added from the studio is not listed twice, and
    title keys so another upload or edition of a title already on the shelf is skipped."""
    seen = set()
    for e in lib.all_entries():
        for u in (e.get("page_url"), (e.get("source") or {}).get("url"), (e.get("rights") or {}).get("source_url")):
            if u:
                seen.add(u)
                m = re.search(r"archive\.org/(?:details|download)/([^/?#]+)", u)
                if m:
                    seen.add(f"archive:{m.group(1)}")
        kind = e.get("kind")
        if kind in ("movie", "series"):
            seen.add(_title_key(kind, e.get("title", ""), e.get("year")))
        elif kind in ("book", "story"):
            seen.add(_title_key(kind, e.get("title", ""), e.get("creator")))
    return seen


def _count(kind: str) -> int:
    return sum(1 for e in lib.all_entries() if e.get("kind") == kind and e.get("visibility", "public") == "public")


def _entry(kind: str, title: str, page_url: str, source: Dict[str, Any], lic_label: str, attribution: str, creator: str = "",
           year: Optional[int] = None, description: str = "", series: Optional[str] = None, episode: Optional[int] = None,
           priority: float = 0, build: Optional[Dict[str, Any]] = None, verified: bool = True) -> Dict[str, Any]:
    return {"id": lib.new_id(title), "kind": kind, "title": title[:150], "series": series, "episode": episode, "creator": creator[:120],
            "year": year, "description": description[:300], "languages": [], "status": "catalog", "source": source, "page_url": page_url,
            "rights": {"status": "cleared", "license": lic_label, "attribution": attribution[:400], "source_url": page_url,
                       "country": rights.COUNTRY, "verified": verified},
            "visibility": "public", "minutes": round((source.get("duration") or 0) / 60, 1) or None, "priority": priority,
            "build": dict({"state": "queued", "attempts": 0, "requested": 0}, **(build or {})), "ai_label": lib.AI_LABEL,
            "origin": "catalog", "created": time.time()}


# ------------------------------------------------------------------ Internet Archive
def _archive_docs(query: str, want: Optional[int] = None) -> List[Dict[str, Any]]:
    """Search results, most downloaded first: up to `want`, or every match when want is None."""
    docs: List[Dict[str, Any]] = []
    page = 1
    while want is None or len(docs) < want:
        params = [("q", query), ("rows", "500"), ("page", str(page)), ("sort[]", "downloads desc"), ("output", "json")]
        params += [("fl[]", f) for f in ("identifier", "title", "year", "date", "creator", "licenseurl", "downloads", "subject")]
        r = httpx.get(ARCHIVE, headers=UA, timeout=60, params=params)
        batch = (r.json().get("response") or {}).get("docs") or []
        docs += batch
        if len(batch) < 500:
            break
        page += 1
    return docs


def pick_archive_media(ident: str) -> Optional[Dict[str, Any]]:
    """The item's full-length MP4 that streams fastest (the smallest file among the longest ones)."""
    try:
        meta = httpx.get(f"https://archive.org/metadata/{quote(ident)}", headers=UA, timeout=40).json()
    except (httpx.HTTPError, ValueError):
        return None
    files = []
    for f in meta.get("files") or []:
        name = str(f.get("name", ""))
        if name.lower().endswith(".mp4"):
            length = lib._seconds(f.get("length"))
            files.append((length, int(f.get("size") or 0), name))
    if not files:
        return None
    longest = max(length for length, _, _ in files)
    good = [f for f in files if f[0] >= 0.6 * longest] or files
    length, _, name = min(good, key=lambda f: f[1] or 10 ** 12)
    return {"backend": "html5", "url": f"https://archive.org/download/{quote(ident)}/{quote(name)}", "duration": round(length or longest, 1)}


def _series_name(title: str, subjects: Any) -> str:
    t = re.sub(r"\s+", " ", title).strip()
    subs = subjects if isinstance(subjects, list) else [s.strip() for s in str(subjects or "").split(";") if s.strip()]
    generic = {"tv", "television", "classic tv", "classic television", "series", "tv series", "comedy", "western", "drama", "sitcom",
               "60's television", "50's television", "live tv", "public domain", "scifi", "sci-fi", "mystery", "variety", "episode"}
    for s in subs:
        s = s.strip()
        if 3 < len(s) < 50 and s.lower() not in generic and s.lower() in t.lower():
            return s
    m = re.match(r'^"?([^":#(]{3,60}?)"?\s*(?:[-:#(]|\bepisode\b|\bep\.)', t, re.I)
    if m:
        return m.group(1).strip().strip('"')
    return re.sub(r"\s*(tv show|tv series|episode.*)$", "", t, flags=re.I).strip()[:60] or t[:60]


def _archive_kind(kind: str, want: Optional[int], seen: set, progress: Callable[[str], None],
                  save: Callable[[Dict[str, Any]], None]) -> int:
    """Adds up to `want` films or episodes (every one found when want is None), saving as it goes. Returns the count."""
    cutoff = rights.this_year() - 61
    coll = FILM_COLLECTIONS if kind == "movie" else "classic_tv"
    docs = _archive_docs(f"collection:{coll} AND mediatype:movies AND {LICENSED} AND year:[1890 TO {cutoff}]",
                         None if want is None else int(want * 1.8))
    cands = []
    for d in docs:
        url = f"https://archive.org/details/{d['identifier']}"
        lic = rights.classify(d.get("licenseurl"))
        year = rights._year(d.get("year") or d.get("date"))
        title = _clean(d.get("title")) or d["identifier"]
        key = _title_key(kind, title, year)
        if url in seen or f"archive:{d['identifier']}" in seen or key in seen or not year or year > cutoff:
            continue
        if not lic["adapt"] or not lic["commercial"]:
            continue                      # a paid library: nothing marked NonCommercial or NoDerivatives
        if MATURE_RE.search(title + " " + _clean(d.get("subject"))):
            continue                      # a family library: no sexual exploitation films
        cands.append((d, lic, year, url, title, key))
    limit = len(cands) if want is None else min(len(cands), int(want * 1.4))
    progress(f"{kind}: {len(cands)} candidates, finding streamable files...")
    added = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i in range(0, limit, 240):
            chunk = cands[i: i + 240]
            medias = list(pool.map(lambda c: pick_archive_media(c[0]["identifier"]), chunk))
            for (d, lic, year, url, title, key), media in zip(chunk, medias):
                if key in seen or not media or (media.get("duration") or 0) < (600 if kind == "movie" else 300):
                    continue              # the most downloaded upload of a title wins; later uploads of it are skipped
                creator = _clean(d.get("creator"))
                series = _series_name(title, d.get("subject")) if kind == "series" else None
                lic_label = lic["label"] if lic["kind"] not in ("public_domain", "cc0") else "Public domain in India (published by 1965)"
                desc = (f"A {year} film" + (f" by {creator}" if creator else "") + ".") if kind == "movie" else f"An episode of {series} ({year})."
                save(_entry(kind, title, url, media, lic_label,
                            f'"{title}" ({year}){", " + creator if creator else ""}, {lic_label}, Internet Archive',
                            creator, year, desc, series=series, priority=float(d.get("downloads") or 0) / 1000.0))
                seen.update((url, key, f"archive:{d['identifier']}"))
                added += 1
                if want is not None and added >= want:
                    return added
            progress(f"{kind}: {added} added, {min(i + 240, limit)} of {limit} checked")
    return added


def number_episodes(entries: List[Dict[str, Any]]) -> None:
    by_show: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in entries:
        by_show[re.sub(r"[^a-z0-9]+", " ", (e.get("series") or "").lower()).strip()].append(e)
    for eps in by_show.values():
        names = Counter(e["series"] for e in eps)
        best = max(names, key=lambda n: (names[n], n != n.lower(), -len(n)))      # the most common spelling, capitalised
        if best == best.lower():
            best = best.title()
        for n, e in enumerate(sorted(eps, key=lambda e: (e.get("year") or 0, e["title"])), 1):
            e["series"], e["episode"] = best, n


# ------------------------------------------------------------------ Project Gutenberg
def _death_years(authors: str) -> Optional[List[int]]:
    parts = [p.strip() for p in (authors or "").split(";") if p.strip()]
    if not parts:
        return None
    out = []
    for p in parts:
        m = re.search(r"(\d{1,4})\??\s*(BCE)?\s*-\s*(\d{1,4})\??\s*(BCE)?", p)
        if m:
            out.append(-int(m.group(3)) if m.group(4) else int(m.group(3)))
            continue
        m = re.search(r",\s*-\s*(\d{3,4})", p)
        if m:
            out.append(int(m.group(1)))
            continue
        m = re.search(r"(\d{1,2})(?:st|nd|rd|th) cent", p, re.I)
        if m:
            out.append(-int(m.group(1)) * 100 if ("BCE" in p or "B.C" in p) else int(m.group(1)) * 100)
            continue
        return None                          # a death year is unknown, so India status cannot be confirmed
    return out


def _short_authors(authors: str) -> str:
    names = []
    for p in (authors or "").split(";"):
        p = re.sub(r"\[.*?\]|\d.*$", "", p).strip(" ,")
        if "," in p:
            last, first = p.split(",", 1)
            p = f"{first.strip()} {last.strip()}"
        if p:
            names.append(re.sub(r"\s*\(.*?\)", "", p).strip())
    return ", ".join(names[:2])


def _gutenberg_rows(progress: Callable[[str], None]) -> List[Dict[str, str]]:
    """The full Project Gutenberg catalogue (about 80,000 rows), downloaded at most once a week."""
    cache = TEMP_DIR / "pg_catalog.csv.gz"
    if not cache.exists() or time.time() - cache.stat().st_mtime > 7 * 86400:
        progress("books and stories: downloading the Project Gutenberg catalogue...")
        cache.write_bytes(httpx.get(GUTENBERG_CSV, headers=UA, timeout=180, follow_redirects=True).content)
    return list(csv.DictReader(io.StringIO(gzip.decompress(cache.read_bytes()).decode("utf-8", errors="replace"))))


def _gutenberg(want_books: Optional[int], want_stories: Optional[int], seen: set, progress: Callable[[str], None],
               save: Callable[[Dict[str, Any]], None]) -> int:
    """English and Hindi books (non-fiction) and stories (fiction) that are public domain in India; None = all of them."""
    rows = _gutenberg_rows(progress)
    progress(f"books and stories: checking {len(rows)} catalogue rows...")
    cutoff = rights.this_year() - 61
    by_author = Counter(r.get("Authors", "").split(";")[0].strip() for r in rows)
    best: Dict[str, tuple] = {}
    for r in rows:
        if r.get("Type") != "Text" or r.get("Language", "") not in ("en", "hi"):
            continue
        url = f"https://www.gutenberg.org/ebooks/{r['Text#']}"
        if url in seen:
            continue
        deaths = _death_years(r.get("Authors", ""))
        if not deaths or max(deaths) > cutoff:
            continue
        subjects, shelves, locc = r.get("Subjects", ""), r.get("Bookshelves", ""), (r.get("LoCC") or "").strip()
        if FICTION_RE.search(subjects + " " + shelves):
            kind = "story"
        elif locc[:1] in NONFICTION_LOCC and "poetry" not in subjects.lower():
            kind = "book"
        else:
            continue
        title = _clean(r.get("Title")).split("\n")[0][:150]
        who = _short_authors(r.get("Authors", ""))
        key = _title_key(kind, title, who)
        if key in seen:
            continue
        score = 10 * ("Best Books Ever" in shelves) + 6 * ("Harvard Classics" in shelves) + 3 * ("Movie Books" in shelves)
        score += 8 * bool(re.search(r"\bIndia\b|Hindu|Buddh|Sanskrit|Bengal|Upanishad|Vedas", subjects + shelves))
        score += 10 * (r.get("Language") == "hi") + min(8, by_author[r.get("Authors", "").split(";")[0].strip()])
        num = int(r["Text#"]) if str(r.get("Text#", "")).isdigit() else 10 ** 9
        cur = best.get(key)
        if cur is None or (score, -num) > (cur[0], -cur[1]):     # of several editions, the best scored, then the first
            best[key] = (score, num, r, url, kind, title, who, key, max(deaths))
    wants = {"book": want_books, "story": want_stories}
    added: Counter = Counter()
    for score, num, r, url, kind, title, who, key, death in sorted(best.values(), key=lambda x: (-x[0], x[1])):
        if wants[kind] is not None and added[kind] >= wants[kind]:
            continue
        subj = [s.strip() for s in (r.get("Subjects") or "").split(";") if s.strip()][:2]
        desc = f"{'A work' if kind == 'book' else 'A story'} by {who or 'an unknown author'}" + (f". Subjects: {'; '.join(subj)}" if subj else "") + "."
        e = _entry(kind, title, url, {"backend": "none", "url": url}, "Public domain in India",
                   f'"{title}" by {who}, Project Gutenberg eBook #{r["Text#"]}, public domain in India', who, None, desc,
                   priority=float(score), build={"gutenberg_id": num})
        e["rights"]["death_year"] = death          # the last author or translator to die: decides the UK (70-year) status
        save(e)
        seen.update((url, key))
        added[kind] += 1
        if sum(added.values()) % 2000 == 0:
            progress(f"books and stories: {added['book']} books and {added['story']} stories added")
    return sum(added.values())


# ------------------------------------------------------------------ Hindi Wikisource
WIKISOURCE = "https://hi.wikisource.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
WS_NONFICTION_RE = re.compile(r"इतिहास|निबंध|विचार|आलोचना|जीवनी|भूमिका|व्याख्यान|समीक्षा|दर्शन|शास्त्र|पत्र")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _ws(params: Dict[str, Any], api: str = WIKISOURCE) -> Dict[str, Any]:
    # POST: fifty Hindi titles, percent-encoded, are too long for a URL (the API reads POSTed queries the same way)
    for attempt in range(4):
        try:
            r = httpx.post(api, data=dict(params, format="json", formatversion=2), headers=UA, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError("busy", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError):
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))      # the wikis ask clients to back off when they are busy
    return {}


def _ws_all(params: Dict[str, Any], key: str, api: str = WIKISOURCE) -> List[Dict[str, Any]]:
    """Every result of a list query, following continuation."""
    out: List[Dict[str, Any]] = []
    cont: Dict[str, Any] = {}
    while True:
        d = _ws(dict(params, **cont), api)
        out += (d.get("query") or {}).get(key) or []
        if "continue" not in d:
            return out
        cont = d["continue"]


def _ws_pages(titles: List[str], api: str = WIKISOURCE, **params: Any) -> tuple:
    """Page records for many titles (50 per request), list properties merged across continuations. Returns
    (pages by final title, requested title -> final title after normalisation and redirects)."""
    pages: Dict[str, Dict[str, Any]] = {}
    alias: Dict[str, str] = {}
    for i in range(0, len(titles), 50):
        cont: Dict[str, Any] = {}
        while True:
            d = _ws(dict(params, action="query", titles="|".join(titles[i:i + 50]), **cont), api)
            q = d.get("query") or {}
            for m in (q.get("normalized") or []) + (q.get("redirects") or []):
                alias[m["from"]] = m["to"]
            for p in q.get("pages") or []:
                cur = pages.setdefault(p["title"], {})
                for k, v in p.items():
                    if isinstance(v, list):
                        cur.setdefault(k, []).extend(v)
                    else:
                        cur[k] = v
            if "continue" not in d:
                break
            cont = d["continue"]
    final = {}
    for t in titles:
        f = t
        for _ in range(3):
            f = alias.get(f, f)
        final[t] = f
    return pages, final


def _wikidata_deaths(qids: List[str]) -> Dict[str, Optional[int]]:
    """Latest recorded year of death per Wikidata item (negative for BCE); None when none is recorded."""
    out: Dict[str, Optional[int]] = {}
    for i in range(0, len(qids), 50):
        d = _ws({"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]), "props": "claims"}, api=WIKIDATA)
        for q, ent in (d.get("entities") or {}).items():
            years = []
            for c in (ent.get("claims") or {}).get("P570") or []:
                t = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("time") or ""
                m = re.match(r"([+-])(\d+)-", t)
                if m:
                    years.append(int(m.group(2)) * (-1 if m.group(1) == "-" else 1))
            out[q] = max(years) if years else None
    return out


class _TextOnly(HTMLParser):
    """Reading text from a rendered Wikisource page, without headers, page numbers, edit links and notes."""
    SKIP = ("noexport", "pagenum", "mw-editsection", "reference", "noprint", "header", "mw-cite-backlink", "navigation")
    VOID = ("br", "img", "hr", "meta", "link", "input", "wbr", "source", "area", "col")

    def __init__(self) -> None:
        super().__init__()
        self.depth, self.stack, self.parts = 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if tag == "br" and not self.depth:
                self.parts.append("\n")
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        skip = self.depth > 0 or tag in ("style", "script", "sup") or any(s in cls for s in self.SKIP) or a.get("id") == "headertemplate"
        self.stack.append(skip)
        if skip:
            self.depth += 1
        elif tag in ("p", "div", "h2", "h3", "h4", "li", "tr", "dd"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.VOID or not self.stack:
            return
        if self.stack.pop():
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def _ws_parse(title: str, api: str = WIKISOURCE) -> str:
    d = _ws({"action": "parse", "page": title, "prop": "text", "redirects": 1}, api)
    p = _TextOnly()
    p.feed((d.get("parse") or {}).get("text") or "")
    text = re.sub(r"[ \t ]+", " ", "".join(p.parts))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _natural(title: str) -> list:
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", title.translate(_DEVANAGARI_DIGITS))]


def _ws_subpages(title: str, api: str = WIKISOURCE) -> List[str]:
    return sorted((p["title"] for p in _ws_all({"action": "query", "list": "allpages", "apprefix": title + "/", "aplimit": "max"},
                                               "allpages", api)), key=_natural)


def wikisource_text(title: str, max_parts: int = 150, api: str = WIKISOURCE) -> str:
    """The plain text of a Wikisource work (Hindi by default): the page and, for a book split into chapters or an opinion
    with separate concurring and dissenting parts, every part in order."""
    subs = sorted(_ws_subpages(title, api), key=lambda t: (0 if "Syllabus" in t else 1 if "Opinion of the Court" in t else 2, _natural(t)))
    pages = [title] + subs[:max_parts]         # a court's own opinion before concurrences and dissents
    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(lambda t: _ws_parse(t, api), pages))
    return "\n\n".join(p for p in parts if p)


HINDI_FAMILY = {"Q1568", "Q11051", "Q1617", "Q35243", "Q29579", "Q33268", "Q36109"}   # Hindi, Hindustani, Urdu, Braj, Awadhi, Bhojpuri, Maithili
TRANSLATOR_RE = re.compile(r"(?:अनुवादक|translator)\s*=\s*[^|}\s]", re.I)


def _wikidata_languages(qids: List[str]) -> Dict[str, set]:
    """Languages each person wrote or spoke in (Wikidata P1412), as item ids."""
    out: Dict[str, set] = {}
    for i in range(0, len(qids), 50):
        d = _ws({"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]), "props": "claims"}, api=WIKIDATA)
        for q, ent in (d.get("entities") or {}).items():
            out[q] = {(((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("id")
                      for c in (ent.get("claims") or {}).get("P1412") or []} - {None}
    return out


def _names_translator(page: Dict[str, Any]) -> bool:
    revs = page.get("revisions") or []
    text = (((revs[0].get("slots") or {}).get("main") or {}).get("content") or "") if revs else ""
    return bool(TRANSLATOR_RE.search(text))


def _wikisource(want: Optional[int], seen: set, progress: Callable[[str], None], save: Callable[[Dict[str, Any]], None]) -> int:
    """Hindi classics on Hindi Wikisource by authors who died by 1965 (death years from Wikidata). A novel is one title;
    a collection whose stories the author page lists one by one becomes one title per story. Translations are left out:
    a translator has rights of their own, and their death years are not recorded here."""
    progress("Hindi classics: reading Hindi Wikisource authors...")
    cutoff = rights.this_year() - 61
    authors = [p["title"] for p in _ws_all({"action": "query", "list": "allpages", "apnamespace": 100, "aplimit": "max"}, "allpages")]
    pages, _ = _ws_pages(authors, prop="pageprops|links", ppprop="wikibase_item", plnamespace=0, pllimit="max")
    qid = {t: (p.get("pageprops") or {}).get("wikibase_item") for t, p in pages.items()}
    people = sorted({q for q in qid.values() if q})
    deaths, langs = _wikidata_deaths(people), _wikidata_languages(people)
    works = []
    for t, p in pages.items():
        death = deaths.get(qid.get(t) or "")
        if death is None or death > cutoff:
            continue                          # living, or no recorded death year: India status cannot be confirmed
        wrote = langs.get(qid.get(t) or "") or set()
        name = t.split(":", 1)[-1]
        if (wrote and not wrote & HINDI_FAMILY) or name in ("मोहनदास करमचंद गांधी",):
            continue                          # wrote in another language (Gandhi: Gujarati, English), so the Hindi text is a translation
        linked = [link["title"] for link in p.get("links") or []]
        for w in linked:
            if "/" not in w and any(x.startswith(w + "/") for x in linked):
                continue                      # a collection whose stories are listed one by one
            works.append((w, name, death))
    info, final = _ws_pages(sorted({w for w, _, _ in works}), prop="info|revisions", rvprop="content", rvslots="main")
    works = [(final[w], name, death) for w, name, death in works
             if "missing" not in info.get(final[w], {"missing": True}) and not _names_translator(info[final[w]])]
    progress(f"Hindi classics: {len(works)} works by {len({n for _, n, _ in works})} authors; checking their length...")

    def measure(w: str) -> tuple:
        subs = _ws_subpages(w) if "/" not in w else []
        if subs:
            return len(subs), 2500 * len(subs)
        try:
            return 0, len(_ws_parse(w).split())
        except (httpx.HTTPError, ValueError):
            return 0, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        sizes = list(pool.map(lambda x: measure(x[0]), works))
    added = 0
    for (w, name, death), (chapters, words) in zip(works, sizes):
        if words < 1000:
            continue                          # poems and fragments: too short for a narrated summary
        kind = "book" if WS_NONFICTION_RE.search(w) else "story"
        coll, _, piece = w.partition("/")
        title = f"{piece.strip()} ({coll.strip()})" if piece else w
        url = "https://hi.wikisource.org/wiki/" + quote(w.replace(" ", "_"))
        key = _title_key(kind, title, name)
        if url in seen or key in seen:
            continue
        desc = (f"{'A work' if kind == 'book' else 'A story'} in Hindi by {name}" + (f", in {chapters} chapters" if chapters else "")
                + f" (public domain in India: the author died in {death}).")
        e = _entry(kind, title, url, {"backend": "none", "url": url}, "Public domain in India",
                   f'"{title}" by {name}, Hindi Wikisource, public domain in India (the author died in {death})', name, None, desc,
                   priority=35.0, build={"wikisource": w, "death_year": death})
        e["rights"]["death_year"] = death
        save(e)
        seen.update((url, key))
        added += 1
        if want is not None and added >= want:
            break
    return added


# ------------------------------------------------------------------ YouTube (Creative Commons)
def _yt_search(q: str, n: int) -> List[Dict[str, Any]]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(q)}&sp=EgIwAQ%3D%3D"
    try:
        res = subprocess.run(["yt-dlp", "--flat-playlist", "-j", "--playlist-end", str(n), url], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=240)
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in res.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("id") and len(d["id"]) == 11:
            out.append(d)
    return out


def _lecture_queries() -> List[tuple]:
    """(search, topic, in Hindi) for every subject, as '<subject> lecture' and 'nptel <subject>' (IIT and IISc courses)."""
    qs = [(f"{t} lecture", t, False) for t in LECTURE_TOPICS] + [(f"nptel {t}", t, False) for t in LECTURE_TOPICS]
    qs += [(f"{t} lecture", t.replace(" in hindi", ""), True) for t in HINDI_LECTURE_TOPICS]
    return qs


def _lectures(want: Optional[int], seen: set, progress: Callable[[str], None], save: Callable[[Dict[str, Any]], None]) -> int:
    queries = _lecture_queries()
    progress(f"lectures: {len(queries)} Creative Commons searches on YouTube...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda q: (q, _yt_search(q[0], 400)), queries))
    cands = []
    for (query, topic, hindi), items in results:
        for rank, d in enumerate(items):
            title = d.get("title") or ""
            channel = d.get("channel") or d.get("uploader") or ""
            dur = d.get("duration") or 0
            url = f"https://www.youtube.com/watch?v={d['id']}"
            if url in seen or not 600 <= dur <= 7200 or rights.REUPLOAD_RE.search(title) or "#short" in title.lower():
                continue
            if NOT_LECTURE_RE.search(title) or not LECTURE_WORDS_RE.search(f"{title} {channel}"):
                continue
            score = 30 - rank / 15 + 6 * bool(re.search(r"lecture|lec\b|class|course|seminar|talk", title, re.I)) + 8 * hindi
            score += 4 * bool(re.search(r"nptel|iit|iisc|university|college|institute|academy", channel, re.I))
            cands.append((score, d, url, topic, hindi, channel))
    progress(f"lectures: {len(cands)} candidates from {sum(len(i) for _, i in results)} search results")
    added, used, per_channel = 0, set(), Counter()
    for score, d, url, topic, hindi, channel in sorted(cands, key=lambda x: -x[0]):
        if url in used or per_channel[channel.lower()] >= CHANNEL_CAP:
            continue
        used.add(url)
        per_channel[channel.lower()] += 1
        title = d.get("title") or url
        if re.search(r"nptel", channel, re.I):      # NPTEL licenses its courses CC BY-SA, though YouTube can only show "CC BY"
            lic_label, credit = "CC BY-SA", f'"{title}" by {channel} (NPTEL), CC BY-SA; the summary is shared under the same licence'
        else:
            lic_label, credit = "CC BY", f'"{title}" by {channel}, CC BY (YouTube)'
        save(_entry("lecture", title, url, {"backend": "youtube", "video_id": d["id"], "duration": d.get("duration")}, lic_label,
                    credit, channel, None, f"A lecture on {topic}"
                    + (" in Hindi" if hindi else "") + (f" by {channel}" if channel else "") + ".",
                    priority=float(score), verified=False))
        seen.add(url)
        added += 1
        if want is not None and added >= want:
            break
    return added


# ------------------------------------------------------------------ run
def harvest(per_type: Optional[int] = None, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
    """Fills the shelves with every title the sources offer (per_type None), or tops each shelf up to per_type."""
    kinds = kinds or ["movie", "series", "book", "story", "lecture"]
    added: Counter = Counter()

    def note(msg: str) -> None:
        STATE["message"] = msg

    def keep(e: Dict[str, Any]) -> None:
        lib.save(e)
        added[e["kind"]] += 1
        STATE["counts"] = {"added": dict(added)}

    def room(kind: str) -> Optional[int]:
        if kind not in kinds:
            return 0
        return None if per_type is None else max(0, per_type - _count(kind))
    with _lock:
        if STATE["running"]:
            return STATE
        STATE.update(running=True, started=time.time(), finished=None, message="Starting...", counts={})
    try:
        seen = _existing_urls()
        for kind in ("movie", "series"):
            if room(kind) != 0:
                _archive_kind(kind, room(kind), seen, note, keep)
        if added["series"]:                  # number every show's episodes again, old and new together
            eps = [e for e in lib.all_entries() if e.get("kind") == "series" and e.get("origin") == "catalog"]
            before = {e["id"]: (e.get("series"), e.get("episode")) for e in eps}
            number_episodes(eps)
            for e in eps:
                if (e["series"], e["episode"]) != before[e["id"]]:
                    lib.save(e)
        if room("book") != 0 or room("story") != 0:
            _gutenberg(room("book"), room("story"), seen, note, keep)
        if room("story") != 0:                # Hindi classics (stories and a few works of non-fiction)
            _wikisource(room("story"), seen, note, keep)
        if room("lecture") != 0:
            _lectures(room("lecture"), seen, note, keep)
        from backend.webapp import sources_intl       # US and UK sources (and everywhere they are free)
        if room("book") != 0:
            sources_intl._scotus(room("book"), seen, note, keep)
            sources_intl._govuk(room("book"), seen, note, keep)
        if room("lecture") != 0:
            sources_intl._nasa(room("lecture"), seen, note, keep)
        if "movie" in kinds or "series" in kinds:
            sources_intl.apply_us_status(note)
        STATE["counts"] = {"added": dict(added), "library": lib.stats()}
        note("Done.")
        return STATE
    except Exception as e:
        note(f"Stopped: {e}")
        raise
    finally:
        STATE.update(running=False, finished=time.time())
