"""
Sources for viewers in the United States and the United Kingdom (and everywhere else they are free).

  US Supreme Court opinions  English Wikisource ("United States Supreme Court decisions"). Court opinions carry no
                             copyright (US government works; judges' opinions are not copyrightable under the government
                             edicts doctrine); they are shelved for US viewers. Summaries are written in English.
  NASA videos                images.nasa.gov videos that have a caption file. US government works, offered everywhere with
                             "Courtesy NASA" and nothing suggesting that NASA endorses CineCut; the summary is written from
                             the captions, so no transcription allowance is used.
  GOV.UK guides              guides and detailed guides published under the Open Government Licence v3.0 (commercial use
                             and adaptation allowed with the licence's attribution statement). Offered in the UK.
  US status from Wikidata    films and episodes whose Wikidata item records "public domain in the United States" for the
                             Internet Archive identifier we list (copyright not renewed, no notice, a federal work, or 95+
                             years old), so US viewers can see them too.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import httpx

from backend.webapp import library as lib
from backend.webapp.catalog import UA, _TextOnly, _entry, _title_key, _ws_all, _ws_pages

EN_WIKISOURCE = "https://en.wikisource.org/w/api.php"
SCOTUS_CATEGORY = "Category:United States Supreme Court decisions"
NASA_API = "https://images-api.nasa.gov"
GOVUK = "https://www.gov.uk"
OGL = "Open Government Licence v3.0"
OGL_STATEMENT = "Contains public sector information licensed under the Open Government Licence v3.0."
US_PD_METHODS = {"Q29941035": "copyright not renewed", "Q47012574": "published without a copyright notice",
                 "Q60671452": "a work of the US federal government", "Q47246828": "published more than 95 years ago"}
MAINTENANCE = ("missing", "Pages with", "PD-", " works", "Index", "Wikisource", "Pages using", "Proofread")


# ------------------------------------------------------------------ US Supreme Court (English Wikisource)
def _scotus(want: Optional[int], seen: set, progress: Callable[[str], None], save: Callable[[Dict[str, Any]], None]) -> int:
    progress("US Supreme Court: listing opinions on English Wikisource...")
    pages = [m["title"] for m in _ws_all({"action": "query", "list": "categorymembers", "cmtitle": SCOTUS_CATEGORY, "cmnamespace": 0,
                                          "cmtype": "page", "cmlimit": "max"}, "categorymembers", api=EN_WIKISOURCE)]
    progress(f"US Supreme Court: {len(pages)} opinions; reading their years...")
    info, final = _ws_pages(pages, api=EN_WIKISOURCE, prop="categories|info", cllimit="max")
    added = 0
    for t in pages:
        p = info.get(final.get(t, t)) or {}
        if "missing" in p or " order (" in t or t.lower().endswith(" order"):
            continue                          # orders on applications are not decisions
        cats = [c["title"].split(":", 1)[-1] for c in p.get("categories") or []]
        year = next((int(c[:4]) for c in cats if c.endswith(" works") and c[:4].isdigit()), None)
        topical = [c for c in cats if not any(m in c for m in MAINTENANCE)]
        title = f"{t} ({year})" if year and str(year) not in t else t
        url = "https://en.wikisource.org/wiki/" + quote(t.replace(" ", "_"))
        key = _title_key("book", title, "scotus")
        if url in seen or key in seen:
            continue
        when = f" ({year})" if year else ""
        e = _entry("book", title, url, {"backend": "none", "url": url}, "Public domain (US court opinion)",
                   f'"{t}", opinion of the Supreme Court of the United States{when}, from Wikisource; court opinions are not copyrighted',
                   "Supreme Court of the United States", year, f"A US Supreme Court decision{when}, explained in plain words.",
                   priority=5.0 + len(topical), build={"wikisource": t, "wikisource_api": EN_WIKISOURCE, "language": "English"})
        e["territories"] = ["US"]              # free everywhere, but shelved for US viewers, who look for them
        save(e)
        seen.update((url, key))
        added += 1
        if want is not None and added >= want:
            break
    progress(f"US Supreme Court: {added} opinions added")
    return added


# ------------------------------------------------------------------ NASA
def srt_text(raw: str) -> str:
    """The spoken words of an SRT caption file."""
    out = []
    for line in (raw or "").replace(chr(0xFEFF), "").splitlines():     # without the byte-order mark
        s = line.strip()
        if not s or s.isdigit() or "-->" in s:
            continue
        out.append(s)
    return " ".join(out)


def _nasa_title(raw: str) -> str:
    """Readable titles from NASA's file-like ones: 'jsc2025m000017_Meet_NASA_Astronaut-1080p' -> 'Meet NASA Astronaut'."""
    t = raw.replace("_", " ").strip()
    t = re.sub(r"^(?:jsc|ksc|msfc|afrc|grc|larc|gsfc|arc|hq|jpl)\d{4}m\d+\s*", "", t, flags=re.I)
    t = re.sub(r"\s*-?\s*(?:4k|2160p|1080p|720p|480p|hd|uhd)\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -")[:150] or raw[:150]


def _https(url: str) -> str:
    return "https://" + url[7:] if url.startswith("http://") else url


def _nasa_page(page: int) -> List[Dict[str, Any]]:
    try:
        r = httpx.get(f"{NASA_API}/search", params={"media_type": "video", "page": page, "page_size": 100}, headers=UA, timeout=60)
        return (r.json().get("collection") or {}).get("items") or [] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []


def _nasa_assets(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        files = httpx.get(_https(item["href"]), headers=UA, timeout=40).json()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    srt = next((f for f in files if f.lower().endswith(".srt")), None)
    mp4 = next((f for suffix in ("~medium.mp4", "~mobile.mp4", "~orig.mp4") for f in files if f.endswith(suffix)), None)
    if not srt or not mp4:
        return None
    try:
        words = len(srt_text(httpx.get(_https(srt), headers=UA, timeout=40, follow_redirects=True).text).split())
    except httpx.HTTPError:
        return None
    return {"srt": _https(srt), "mp4": _https(mp4), "words": words}


def nasa_transcript(srt_url: str) -> str:
    return srt_text(httpx.get(srt_url, headers=UA, timeout=60, follow_redirects=True).text)


def _nasa(want: Optional[int], seen: set, progress: Callable[[str], None], save: Callable[[Dict[str, Any]], None]) -> int:
    progress("NASA: listing videos...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        items = [i for page in pool.map(_nasa_page, range(1, 101)) for i in page]
    cands = []
    for it in items:
        d = (it.get("data") or [{}])[0]
        nid = d.get("nasa_id")
        if not nid:
            continue
        url = f"https://images.nasa.gov/details/{quote(nid)}"
        if url not in seen:
            cands.append((it, d, url))
    progress(f"NASA: {len(items)} videos; checking which have captions...")
    added = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i in range(0, len(cands), 400):
            chunk = cands[i:i + 400]
            for (it, d, url), a in zip(chunk, pool.map(lambda c: _nasa_assets(c[0]), chunk)):
                if not a or a["words"] < 600:
                    continue                  # b-roll and short clips: too little said for a summary
                title = _nasa_title(d.get("title") or d["nasa_id"])
                created = str(d.get("date_created") or "")
                year = int(created[:4]) if created[:4].isdigit() else None
                key = _title_key("lecture", title, "nasa")
                if key in seen:
                    continue
                when = f" ({year})" if year else ""
                desc = " ".join((d.get("description") or "").split())
                e = _entry("lecture", title, url, {"backend": "html5", "url": a["mp4"]}, "Public domain (NASA, US government work)",
                           f'"{title}", NASA{when}. Courtesy NASA; NASA does not endorse CineCut.', f"NASA {d.get('center') or ''}".strip(),
                           year, (desc[:280] + "...") if len(desc) > 280 else (desc or "A NASA video, explained."),
                           priority=12.0 + min(8.0, a["words"] / 1000.0), build={"nasa_srt": a["srt"], "language": "English"})
                e["territories"] = ["ALL"]
                save(e)
                seen.update((url, key))
                added += 1
                if want is not None and added >= want:
                    return added
            progress(f"NASA: {added} videos added, {min(i + 400, len(cands))} of {len(cands)} checked")
    return added


# ------------------------------------------------------------------ GOV.UK (Open Government Licence)
def _govuk_results(fmt: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start = 0
    while True:
        try:
            r = httpx.get(f"{GOVUK}/api/search.json", headers=UA, timeout=60,
                          params={"filter_format": fmt, "count": 1000, "start": start, "fields": "title,link,description,public_timestamp"})
            batch = r.json().get("results") or [] if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            batch = []
        out += batch
        if len(batch) < 1000:
            return out
        start += 1000


def _govuk(want: Optional[int], seen: set, progress: Callable[[str], None], save: Callable[[Dict[str, Any]], None]) -> int:
    progress("GOV.UK: listing guides...")
    added = 0
    for fmt, prio in (("guide", 20.0), ("detailed_guide", 10.0)):
        for r in _govuk_results(fmt):
            link = r.get("link") or ""
            if not link.startswith("/"):
                continue
            url = GOVUK + link
            title = (r.get("title") or link).strip()[:150]
            key = _title_key("book", title, "govuk")
            if url in seen or key in seen:
                continue
            e = _entry("book", title, url, {"backend": "none", "url": url}, OGL, f'"{title}", GOV.UK. {OGL_STATEMENT}', "GOV.UK", None,
                       (r.get("description") or "UK government guidance, explained.")[:300], priority=prio,
                       build={"govuk": link, "language": "English"})
            e["territories"] = ["GB"]
            save(e)
            seen.update((url, key))
            added += 1
            if want is not None and added >= want:
                return added
    progress(f"GOV.UK: {added} guides added")
    return added


def _html(value: Any) -> str:
    if isinstance(value, list):             # some formats give the body as a list of content types
        return next((v.get("content") or "" for v in value if isinstance(v, dict) and v.get("content_type") == "text/html"), "")
    return str(value or "")


def govuk_text(link: str) -> str:
    """The text of a GOV.UK guide from the Content API: every part, with its heading."""
    d = httpx.get(f"{GOVUK}/api/content{link}", headers=UA, timeout=60).json()
    det = d.get("details") or {}
    parts = det.get("parts") or []
    html = "".join(f"<h2>{p.get('title', '')}</h2>{_html(p.get('body'))}" for p in parts) if parts else _html(det.get("body"))
    p = _TextOnly()
    p.feed(f"<h1>{d.get('title', '')}</h1>" + html)
    lines = [" ".join(x.split()) for x in "".join(p.parts).splitlines()]
    return "\n".join(x for x in lines if x)


# ------------------------------------------------------------------ US status of films, from Wikidata
def us_status_from_wikidata() -> Dict[str, str]:
    """Internet Archive identifier -> why Wikidata says the work is public domain in the United States."""
    q = ("SELECT ?ia ?method WHERE { ?item wdt:P724 ?ia . ?item p:P6216 ?st . ?st ps:P6216 wd:Q19652 . "
         "?st pq:P1001 wd:Q30 . OPTIONAL { ?st pq:P459 ?method } }")
    r = httpx.get("https://query.wikidata.org/sparql", params={"query": q}, headers=dict(UA, Accept="application/sparql-results+json"),
                  timeout=240)
    out: Dict[str, str] = {}
    for b in r.json()["results"]["bindings"]:
        m = b.get("method", {}).get("value", "").rsplit("/", 1)[-1]
        if m in US_PD_METHODS:
            out[b["ia"]["value"]] = US_PD_METHODS[m]
    return out


def apply_us_status(progress: Callable[[str], None] = print) -> int:
    status = us_status_from_wikidata()
    n = 0
    for e in lib.all_entries():
        pu = e.get("page_url") or ""
        if e.get("kind") not in ("movie", "series") or "archive.org/details/" not in pu:
            continue
        ident = pu.split("archive.org/details/", 1)[1].split("/")[0].split("?")[0]
        basis = status.get(ident)
        note = f"Public domain in the US ({basis}, per Wikidata)" if basis else None
        r = e.setdefault("rights", {})
        if note and r.get("us_public_domain") != note:
            r["us_public_domain"] = note
            lib.save(e)
            n += 1
    progress(f"US status: {n} films and episodes newly confirmed public domain in the US ({len(status)} Wikidata records)")
    return n
