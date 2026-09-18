"""
Free library: finds films, series episodes, lectures, books and audiobooks that are public domain or openly
licensed, so CineCut can make full summaries of them without copyright problems.

Sources (all free, no key needed):
  - Internet Archive: feature films, silent films, classic TV and educational films with a licence URL
  - Wikimedia Commons: videos that are free in the USA and their country of origin
  - YouTube, filtered to Creative Commons (CC BY) uploads; each is re-checked before download
  - Project Gutenberg (through Gutendex): public-domain books with author death years
  - LibriVox: public-domain audiobooks read by volunteers
Every result carries the same rights verdict as rights.check_url, worked out for the configured country.
"""
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, List
from urllib.parse import quote_plus

import httpx

from backend.video_engine import rights

UA = rights.UA
KINDS = ("films", "lectures", "books", "audiobooks", "all")


def _clean(q: str) -> str:
    return re.sub(r"[^\w\s\-']", " ", q or "", flags=re.UNICODE).strip()[:80]


def _item(source: str, kind: str, title: str, url: str, lic: Dict[str, Any], status: str, reason: str = "", **extra) -> Dict[str, Any]:
    return dict({"source": source, "kind": kind, "title": title, "url": url, "license": lic, "status": status, "reason": reason}, **extra)


def archive(q: str, kind: str, limit: int) -> List[Dict[str, Any]]:
    cols = ("collection:(feature_films OR silent_films OR classic_tv OR moviesandfilms OR sci-fi_horror OR film_noir "
            "OR comedy_films OR animationandcartoons)") if kind == "films" else \
           "collection:(educationalfilms OR prelinger OR opensource_movies OR lecturesandtalks OR ucberkeley)"
    query = f"(title:({q}) OR subject:({q})) AND mediatype:movies AND {cols} AND licenseurl:*"
    r = httpx.get("https://archive.org/advancedsearch.php", headers=UA, timeout=25, params=[
        ("q", query), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "year"), ("fl[]", "date"), ("fl[]", "licenseurl"),
        ("fl[]", "creator"), ("fl[]", "runtime"), ("sort[]", "downloads desc"), ("rows", str(limit)), ("output", "json")])
    out = []
    for d in (r.json().get("response") or {}).get("docs") or []:
        lic = rights.classify(d.get("licenseurl"))
        year = rights._year(d.get("year") or d.get("date")) or rights._year(d.get("title"))
        status, reason = ("cleared" if lic["adapt"] else "private"), ""
        if lic["kind"] in ("public_domain", "cc0") and rights.COUNTRY == "IN":
            if rights.pd_by_age(year):
                reason = f"{year}: public domain in India (films are free 60 years after publication)"
            elif year:
                status, reason = "private", f"US public domain only; protected in India until 1 January {year + 61}"
            else:
                status, reason = "private", "No publication year given, so India status cannot be confirmed"
        elif lic["adapt"]:
            reason = lic["label"]
        creator = d.get("creator") or ""
        out.append(_item("Internet Archive", "film" if kind == "films" else "lecture", str(d.get("title") or d["identifier"]),
                         f"https://archive.org/details/{d['identifier']}", lic, status, reason, year=year,
                         creator=", ".join(creator) if isinstance(creator, list) else str(creator), runtime=d.get("runtime")))
    return out


def commons(q: str, limit: int) -> List[Dict[str, Any]]:
    r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=25, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:video {q}", "gsrnamespace": "6",
        "gsrlimit": str(limit), "prop": "imageinfo", "iiprop": "url|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|DateTimeOriginal|Artist|ObjectName"})
    out = []
    for p in ((r.json().get("query") or {}).get("pages") or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        val = lambda k: re.sub(r"<[^>]+>", "", str((meta.get(k) or {}).get("value") or "")).strip()
        lic = rights.classify(val("LicenseShortName"))
        out.append(_item("Wikimedia Commons", "film", val("ObjectName") or p["title"][5:], ii.get("descriptionurl") or
                         f"https://commons.wikimedia.org/wiki/{quote_plus(p['title'])}", lic,
                         "cleared" if lic["adapt"] else "private", lic["label"], year=rights._year(val("DateTimeOriginal")),
                         creator=val("Artist")[:80]))
    return out


def youtube_cc(q: str, limit: int) -> List[Dict[str, Any]]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(q)}&sp=EgIwAQ%3D%3D"
    res = subprocess.run(["yt-dlp", "--flat-playlist", "-j", "--playlist-end", str(limit), url], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=90)
    out = []
    for line in res.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not d.get("id") or len(d["id"]) != 11:
            continue
        title = d.get("title") or ""
        reupload = bool(rights.REUPLOAD_RE.search(title))
        out.append(_item("YouTube (Creative Commons filter)", "lecture", title, f"https://www.youtube.com/watch?v={d['id']}",
                         rights.license_info("cc_by"), "private" if reupload else "cleared",
                         "Looks like a re-upload of a commercial title" if reupload else "CC BY; re-checked before download",
                         creator=d.get("channel") or d.get("uploader") or "", duration_sec=d.get("duration")))
    return out


def _gutenberg_catalogue(q: str, limit: int) -> List[Dict[str, Any]]:
    """Fallback when Gutendex is down: Project Gutenberg's own search feed, then each book's catalogue record."""
    feed = httpx.get("https://www.gutenberg.org/ebooks/search.opds/", headers=UA, timeout=30, params={"query": q},
                     follow_redirects=True).text
    ids = list(dict.fromkeys(int(x) for x in re.findall(r"/ebooks/(\d+)\.opds", feed)))[:limit]
    with ThreadPoolExecutor(max_workers=4) as pool:
        metas = list(pool.map(lambda i: (i, rights.gutenberg_meta(i)), ids))
    return [{"id": i, "title": m["title"], "copyright": m["copyright"], "languages": m["languages"], "download_count": None,
             "authors": [p for p in m["people"] if p["name"] in m["authors"]],
             "translators": [p for p in m["people"] if p["name"] not in m["authors"]]} for i, m in metas]


def gutenberg(q: str, limit: int) -> List[Dict[str, Any]]:
    try:
        r = httpx.get("https://gutendex.com/books/", headers=UA, timeout=6, params={"search": q}, follow_redirects=True)
        books = r.json().get("results") or []
    except (httpx.HTTPError, ValueError):
        books = _gutenberg_catalogue(q, limit)
    out = []
    for b in books[:limit]:
        people = (b.get("authors") or []) + (b.get("translators") or [])
        in_pd = rights.pd_by_death([p.get("death_year") for p in people])
        if b.get("copyright"):
            status, reason = "private", "Still in copyright"
        elif rights.COUNTRY != "IN" or in_pd:
            status, reason = "cleared", "Public domain in India (authors and translators died 60+ years ago)" if rights.COUNTRY == "IN" else "Public domain in the USA"
        else:
            status, reason = "private", "US public domain only: an author or translator died less than 60 years ago (or the year is unknown)"
        out.append(_item("Project Gutenberg", "book", b.get("title", ""), f"https://www.gutenberg.org/ebooks/{b['id']}",
                         rights.license_info("public_domain" if not b.get("copyright") else "standard"), status, reason,
                         creator=", ".join(p.get("name", "") for p in b.get("authors") or []), gutenberg_id=b["id"],
                         languages=b.get("languages") or [], downloads=b.get("download_count")))
    return out


_LIBRIVOX_DEATHS: Dict[str, Any] = {}


def _librivox_death(name: str):
    """The author's year of death from LibriVox's author list (the Internet Archive copies of LibriVox recordings do not
    carry it), so a recording of, say, Conan Doyle (died 1930) can be confirmed as free in India. None when unknown."""
    first = (name or "").replace(" and ", ";").split(";")[0].strip()
    tokens = "".join(ch.lower() if ch.isalpha() else " " for ch in first).split()
    if not tokens:
        return None
    key = " ".join(tokens)
    if key in _LIBRIVOX_DEATHS:
        return _LIBRIVOX_DEATHS[key]
    death = None
    try:
        r = httpx.get("https://librivox.org/api/feed/authors/", headers=UA, timeout=8,
                      params={"last_name": tokens[-1], "format": "json"})
        for a in (r.json().get("authors") or []):
            have = set("".join(ch.lower() if ch.isalpha() else " " for ch in f"{a.get('first_name', '')} {a.get('last_name', '')}").split())
            if set(tokens) <= have or have <= set(tokens):
                dod = str(a.get("dod") or "").strip()
                if dod.lstrip("-").isdigit():
                    death = int(dod)
                    break
    except (httpx.HTTPError, ValueError):
        pass
    _LIBRIVOX_DEATHS[key] = death
    return death


def librivox(q: str, limit: int) -> List[Dict[str, Any]]:
    books: Dict[str, Dict[str, Any]] = {}
    words = q.split()
    # LibriVox matches titles by prefix and authors by last name, so a one-word query may be an author
    searches = [{"title": f"^{q}"}] + ([{"author": words[0]}] if len(words) == 1 else [])
    for params in searches:
        try:
            r = httpx.get("https://librivox.org/api/feed/audiobooks/", headers=UA, timeout=12,
                          params=dict(params, format="json", extended="1", limit=str(limit)))
            for b in (r.json().get("books") or []):
                books.setdefault(b["id"], b)
        except (httpx.HTTPError, ValueError):
            continue
    out = []
    for b in list(books.values())[:limit]:
        deaths = [rights._year(a.get("dod")) for a in b.get("authors") or []]
        in_pd = rights.pd_by_death(deaths)
        status = "cleared" if (rights.COUNTRY != "IN" or in_pd) else "private"
        out.append(_item("LibriVox", "audiobook", b.get("title", ""), b.get("url_librivox") or "", rights.license_info("public_domain"),
                         status, "Recording dedicated to the public domain" + ("" if status == "cleared" else "; text still protected in India"),
                         creator=", ".join(f"{a.get('first_name', '')} {a.get('last_name', '')}".strip() for a in b.get("authors") or []),
                         language=b.get("language"), runtime=b.get("totaltime")))
    # LibriVox's own search only matches the start of a title, so its Internet Archive collection finds the rest
    # ("sherlock holmes" -> "Adventures of Sherlock Holmes"). Those records carry no death years, so India status stays unconfirmed.
    if len(out) < limit:
        try:
            r = httpx.get("https://archive.org/advancedsearch.php", headers=UA, timeout=15, params=[
                ("q", f"collection:librivoxaudio AND title:({q})"), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "creator"),
                ("sort[]", "downloads desc"), ("rows", str(limit)), ("output", "json")])
            seen = {x["title"].lower() for x in out}
            for d in (r.json().get("response") or {}).get("docs") or []:
                title = str(d.get("title") or d["identifier"])
                if title.lower() in seen:
                    continue
                seen.add(title.lower())
                creator = d.get("creator") or ""
                who = ", ".join(creator) if isinstance(creator, list) else str(creator)
                death = _librivox_death(who)
                ok = death is not None and (rights.COUNTRY != "IN" or bool(rights.pd_by_death([death])))
                out.append(_item("LibriVox (Internet Archive)", "audiobook", title, f"https://archive.org/details/{d['identifier']}",
                                 rights.license_info("public_domain"), "cleared" if ok else "private",
                                 "Recording dedicated to the public domain" + (f"; the author died in {death}" if ok else
                                                                               "; the author's death year could not be confirmed, so India status is unconfirmed"),
                                 creator=who))
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return out[:limit]


def search(query: str, kind: str = "all", limit: int = 8) -> Dict[str, Any]:
    q = _clean(query)
    if not q:
        return {"query": query, "results": [], "errors": ["Type something to search for."]}
    kind = kind if kind in KINDS else "all"
    jobs = []
    if kind in ("films", "all"):
        jobs += [("Internet Archive", lambda: archive(q, "films", limit)), ("Wikimedia Commons", lambda: commons(q, limit))]
    if kind in ("lectures", "all"):
        jobs += [("YouTube CC", lambda: youtube_cc(q, limit)), ("Internet Archive (education)", lambda: archive(q, "lectures", limit))]
    if kind in ("books", "all"):
        jobs.append(("Project Gutenberg", lambda: gutenberg(q, limit)))
    if kind in ("audiobooks", "all"):
        jobs.append(("LibriVox", lambda: librivox(q, limit)))
    results, errors = [], []
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futs = [(name, pool.submit(fn)) for name, fn in jobs]
    deadline = time.time() + 15          # a slow source (LibriVox can take 20 s) must not hold up the others
    for name, f in futs:
        try:
            results += f.result(timeout=max(0.5, deadline - time.time()))
        except FuturesTimeout:
            errors.append(f"{name}: took too long, try again or search it alone")
        except Exception as e:  # one source failing should not hide the others
            errors.append(f"{name}: {str(e)[:120]}")
    pool.shutdown(wait=False, cancel_futures=True)
    order = {"cleared": 0, "private": 1}
    results.sort(key=lambda r: order.get(r["status"], 2))
    return {"query": q, "kind": kind, "country": rights.COUNTRY, "results": results, "errors": errors}
