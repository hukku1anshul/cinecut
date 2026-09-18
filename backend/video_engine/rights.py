"""
Rights check for everything CineCut is asked to summarise.

Every link or file gets one of three outcomes before any work starts:
  cleared  - the source itself says the work is public domain, or openly licensed in a way that allows a summary
             (an adaptation). Full features: saving, exports, rendering to the output folder.
  private  - no verified licence (for example YouTube's standard licence), or the licence forbids adaptations
             (NoDerivatives). The summary is shown in the app and deleted afterwards; nothing is exported or kept.
  blocked  - DRM streaming services. CineCut never downloads from them; the watch guide works with them instead.

The checks read machine-readable licence data at the source: YouTube's and Vimeo's licence field (through yt-dlp),
the Internet Archive's licenseurl, Wikimedia Commons' LicenseShortName, and Project Gutenberg's / LibriVox's
author death years. Public-domain status depends on the country: India protects films, sound recordings and
photographs for 60 years from publication and books for the author's life plus 60 years (the USA frees works
95 years after publication). A public-domain film can still carry a newer soundtrack, restoration or subtitles,
and an uploader can mislabel a film, so the result is advice; the Terms keep the user responsible.
"""
import datetime
import json
import os
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, unquote

import httpx

from backend.config import BASE_DIR

COUNTRY = os.environ.get("CINECUT_COUNTRY", "IN").upper()
DATA_DIR = BASE_DIR / "data"
DECLARATIONS_FILE = DATA_DIR / "rights_declarations.jsonl"
# Wikimedia refuses clients whose user agent has no contact address; set CINECUT_CONTACT_EMAIL to give yours.
UA = {"User-Agent": f"CineCut/10.0 (local desktop app; contact: {os.environ.get('CINECUT_CONTACT_EMAIL') or 'owner not configured'}; "
                    f"+https://commons.wikimedia.org/wiki/Commons:API) python-httpx"}

DRM_HOSTS = ("netflix.com", "primevideo.com", "hotstar.com", "jiohotstar.com", "jiocinema.com", "zee5.com", "sonyliv.com",
             "disneyplus.com", "hulu.com", "max.com", "hbomax.com", "tv.apple.com", "mubi.com", "aha.video", "sunnxt.com",
             "erosnow.com", "altbalaji.com", "hoichoi.tv", "mxplayer.in", "voot.com", "paramountplus.com", "peacocktv.com",
             "crunchyroll.com", "shemaroome.com", "lionsgateplay.com", "discoveryplus.in", "airtelxstream.in")

# A Creative Commons badge on a re-upload of a commercial film is almost always wrong: the uploader cannot license
# what they do not own.
REUPLOAD_RE = re.compile(r"full\s*(hd\s*)?(movie|film)|hindi\s+dubbed|superhit|blockbuster|new\s+(south|bollywood|hindi)\s+movie"
                         r"|full\s+episode|all\s+episodes|web\s*series|official\s+trailer", re.I)

LICENSES: Dict[str, Dict[str, Any]] = {
    "public_domain": {"label": "Public domain", "adapt": True, "commercial": True, "attribution": False, "share_alike": False},
    "cc0": {"label": "CC0 (public domain dedication)", "adapt": True, "commercial": True, "attribution": False, "share_alike": False},
    "cc_by": {"label": "CC BY", "adapt": True, "commercial": True, "attribution": True, "share_alike": False},
    "cc_by_sa": {"label": "CC BY-SA", "adapt": True, "commercial": True, "attribution": True, "share_alike": True},
    "cc_by_nc": {"label": "CC BY-NC", "adapt": True, "commercial": False, "attribution": True, "share_alike": False},
    "cc_by_nc_sa": {"label": "CC BY-NC-SA", "adapt": True, "commercial": False, "attribution": True, "share_alike": True},
    "cc_by_nd": {"label": "CC BY-ND", "adapt": False, "commercial": True, "attribution": True, "share_alike": False},
    "cc_by_nc_nd": {"label": "CC BY-NC-ND", "adapt": False, "commercial": False, "attribution": True, "share_alike": False},
    "standard": {"label": "Standard licence (all rights reserved)", "adapt": False, "commercial": False, "attribution": True, "share_alike": False},
    "unknown": {"label": "No licence information", "adapt": False, "commercial": False, "attribution": True, "share_alike": False},
}

_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
RIGHTS_BY_PATH: Dict[str, Dict[str, Any]] = {}     # downloaded file -> rights report (set by the download step)


def this_year() -> int:
    return datetime.date.today().year


def license_info(kind: str) -> Dict[str, Any]:
    return dict(LICENSES.get(kind, LICENSES["unknown"]), kind=kind)


def classify(text: Optional[str]) -> Dict[str, Any]:
    """Licence kind from a licence URL, short name or sentence ('CC BY-SA 4.0', 'Creative Commons Attribution
    license (reuse allowed)', 'http://creativecommons.org/licenses/by-nc/3.0/', 'Public domain')."""
    t = (text or "").strip().lower()
    if not t:
        return license_info("unknown")
    if "publicdomain/zero" in t or re.search(r"\bcc0\b|cc-zero", t):
        return license_info("cc0")
    if "publicdomain" in t or "public domain" in t or "public-domain" in t or re.fullmatch(r"pd(-\w+)*", t):
        return license_info("public_domain")
    code = None
    m = re.search(r"licenses/([a-z\-]+)", t)
    if m:
        code = m.group(1)
    else:
        m = re.search(r"\bcc[ \-_]?(by(?:[ \-_](?:nc|nd|sa))*)", t)
        if m:
            code = m.group(1)
        elif "creative commons" in t or "creativecommons" in t:
            code = "by"
            if "noncommercial" in t or "non-commercial" in t:
                code += "-nc"
            if "noderiv" in t or "no deriv" in t:
                code += "-nd"
            if "sharealike" in t or "share-alike" in t or "share alike" in t:
                code += "-sa"
    if code:
        parts = set(re.split(r"[ \-_]", code))
        kind = "cc_by" + ("_nc" if "nc" in parts else "") + ("_nd" if "nd" in parts else "") + ("_sa" if "sa" in parts and "nd" not in parts else "")
        return license_info(kind if kind in LICENSES else "cc_by")
    if "gfdl" in t or "gnu free documentation" in t:
        return dict(license_info("cc_by_sa"), label="GFDL (treated like CC BY-SA)")
    if "standard youtube" in t or "all rights reserved" in t or "copyright" in t:
        return license_info("standard")
    return license_info("unknown")


def pd_by_age(year: Optional[int], country: str = COUNTRY) -> bool:
    """Films, sound recordings and photographs: India 60 years counted from the year after publication (1965 works
    became free on 1 January 2026); USA 95 years from publication."""
    if not year:
        return False
    return int(year) <= this_year() - (61 if country == "IN" else 96)


def pd_by_death(death_years: List[Optional[int]], country: str = COUNTRY) -> Optional[bool]:
    """Books: India life of the author (and of any translator) plus 60 years. None when a death year is unknown."""
    if not death_years:
        return None
    if any(d is None for d in death_years):
        return None
    return all(int(d) <= this_year() - 61 for d in death_years) if country == "IN" else None


def _year(value: Any) -> Optional[int]:
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(value or ""))
    return int(m.group(1)) if m else None


def _report(url: str, host: str, source: str, lic: Dict[str, Any], title: str = "", creator: str = "",
            year: Optional[int] = None, reasons: Optional[List[str]] = None, caveats: Optional[List[str]] = None,
            status: Optional[str] = None, kind: str = "video") -> Dict[str, Any]:
    reasons, caveats = list(reasons or []), list(caveats or [])
    if status is None:
        status = "cleared" if lic.get("adapt") else "private"
        if lic["kind"] in ("cc_by_nd", "cc_by_nc_nd"):
            reasons.append("The licence allows sharing the original unchanged but not summaries or edits, so this stays in private viewing.")
        elif lic["kind"] in ("standard", "unknown"):
            reasons.append("No open licence was found at the source, so the summary is private: shown here, then deleted.")
    if status == "cleared" and lic.get("attribution"):
        caveats.append("Credit the creator and link the licence wherever the summary is shown.")
    if status == "cleared" and not lic.get("commercial", True):
        caveats.append("Non-commercial licence: no ads, sponsorships or paid distribution of the summary.")
    if status == "cleared" and lic.get("share_alike"):
        caveats.append("Share-alike: the summary must be released under the same licence.")
    if status == "cleared" and kind in ("video", "film"):
        caveats.append("Music, a newer restoration, dubbing or subtitles can have their own copyright; check before publishing.")
    lic_url = {"cc_by": "https://creativecommons.org/licenses/by/4.0/", "cc_by_sa": "https://creativecommons.org/licenses/by-sa/4.0/",
               "cc_by_nc": "https://creativecommons.org/licenses/by-nc/4.0/", "cc_by_nc_sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/"}.get(lic["kind"])
    attribution = f'"{title or "Untitled"}"' + (f" by {creator}" if creator else "") + (f" ({year})" if year else "") + \
        f", {lic['label']}" + (f" ({lic_url})" if lic_url else "") + f", source: {url}"
    return {"url": url, "host": host, "source": source, "status": status, "license": lic, "title": title, "creator": creator,
            "year": year, "reasons": reasons, "caveats": caveats, "attribution": attribution,
            "noncommercial": not lic.get("commercial", True), "share_alike": bool(lic.get("share_alike")),
            "country": COUNTRY, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def blocked_host(host: str, path: str = "") -> bool:
    h = host.lower()
    if any(h == d or h.endswith("." + d) for d in DRM_HOSTS):
        return True
    return bool(re.match(r"(www\.)?amazon\.[a-z.]+$", h) and ("video" in path.lower() or "/dp/" in path.lower()))


# ------------------------------------------------------------------ per source
def check_archive(identifier: str, url: str) -> Dict[str, Any]:
    d = httpx.get(f"https://archive.org/metadata/{identifier}", headers=UA, timeout=30).json().get("metadata") or {}
    title, creator = str(d.get("title") or identifier), d.get("creator") or ""
    creator = ", ".join(creator) if isinstance(creator, list) else str(creator)
    year = _year(d.get("year") or d.get("date")) or _year(title)
    lic = classify(d.get("licenseurl") or d.get("rights") or "")
    reasons = []
    if lic["kind"] in ("public_domain", "cc0") and COUNTRY == "IN":
        if pd_by_age(year):
            reasons.append(f"Published {year}: films become free in India 60 years after publication, so this is public domain in India.")
        else:
            reasons.append(f"Marked public domain on the Internet Archive (usually a US status). In India a film published in {year} "
                           f"is protected until 1 January {year + 61}, so it stays private here." if year else
                           "Marked public domain on the Internet Archive (usually a US status), but no publication year is given, "
                           "so its status in India cannot be confirmed. It stays private here.")
            return _report(url, "archive.org", "Internet Archive licence field", lic, title, creator, year, reasons, status="private")
    return _report(url, "archive.org", "Internet Archive licence field", lic, title, creator, year, reasons)


def check_commons(filename: str, url: str) -> Dict[str, Any]:
    title = filename if filename.lower().startswith("file:") else f"File:{filename}"
    r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
        "action": "query", "format": "json", "titles": title, "prop": "imageinfo", "iiprop": "extmetadata|url",
        "iiextmetadatafilter": "LicenseShortName|DateTimeOriginal|Artist|ObjectName"})
    pages = (r.json().get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), {})
    meta = ((page.get("imageinfo") or [{}])[0]).get("extmetadata") or {}
    val = lambda k: re.sub(r"<[^>]+>", "", str((meta.get(k) or {}).get("value") or "")).strip()
    lic = classify(val("LicenseShortName"))
    reasons = ["Wikimedia Commons accepts only files that are free in the USA and in their country of origin."] if lic["adapt"] else []
    return _report(url, "commons.wikimedia.org", "Wikimedia Commons licence", lic, val("ObjectName") or title[5:],
                   val("Artist"), _year(val("DateTimeOriginal")), reasons)


def _estimated_death(name: str, birth: Optional[int]) -> Optional[int]:
    """Catalogues often give ancient and early authors no death date ("Sunzi, active 6th century B.C.")."""
    if birth is not None and birth <= this_year() - 171:      # born 171+ years ago, so dead for well over 60 years
        return birth + 110
    m = re.search(r"(\d+)(?:st|nd|rd|th) cent", name)
    if m and ("B.C" in name or int(m.group(1)) <= 18):
        return (-1 if "B.C" in name else 1) * int(m.group(1)) * 100
    return -1 if "B.C" in name else None


def gutenberg_meta(book_id: int) -> Dict[str, Any]:
    """Title, rights statement, every person credited (authors, translators, editors, introductions) with death
    years, and languages, from Project Gutenberg's own catalogue record."""
    import html as _html
    x = httpx.get(f"https://www.gutenberg.org/cache/epub/{int(book_id)}/pg{int(book_id)}.rdf", headers=UA, timeout=40,
                  follow_redirects=True).text
    people: Dict[str, Optional[int]] = {}
    for block in re.findall(r"<pgterms:agent .*?</pgterms:agent>", x, re.S):
        name = re.search(r"<pgterms:name>([^<]*)", block)
        death = re.search(r"<pgterms:deathdate[^>]*>(-?\d+)", block)
        birth = re.search(r"<pgterms:birthdate[^>]*>(-?\d+)", block)
        nm = _html.unescape(name.group(1)) if name else "?"
        people[nm] = int(death.group(1)) if death else _estimated_death(nm, int(birth.group(1)) if birth else None)
    authors = [_html.unescape(n) for n in re.findall(r"<dcterms:creator>\s*<pgterms:agent .*?<pgterms:name>([^<]*)", x, re.S)]
    title = re.search(r"<dcterms:title>([^<]*)", x)
    rights_txt = (re.search(r"<dcterms:rights>([^<]*)", x) or [None, ""])[1]
    return {"title": _html.unescape(title.group(1)).strip() if title else f"Gutenberg book {book_id}",
            "copyright": not rights_txt.lower().startswith("public domain"), "rights": rights_txt,
            "people": [{"name": n, "death_year": d} for n, d in people.items()], "authors": authors,
            "languages": re.findall(r"<dcterms:language>.*?<rdf:value[^>]*>([^<]*)", x, re.S)}


def check_gutenberg(book_id: int, url: Optional[str] = None) -> Dict[str, Any]:
    url = url or f"https://www.gutenberg.org/ebooks/{book_id}"
    b = gutenberg_meta(book_id)
    people = b["people"]
    creator = ", ".join(b["authors"])
    lic = license_info("public_domain" if not b["copyright"] else "standard")
    reasons = []
    if b["copyright"]:
        return _report(url, "gutenberg.org", "Project Gutenberg rights statement", lic, b["title"], creator,
                       None, ["Project Gutenberg marks this book as still in copyright."], kind="book")
    if COUNTRY == "IN":
        if pd_by_death([p["death_year"] for p in people]):
            reasons.append("Every author, translator and other contributor died more than 60 years ago, so the text is public domain in India.")
        else:
            late = [f"{p['name']} ({p['death_year'] or 'death year unknown'})" for p in people
                    if p["death_year"] is None or p["death_year"] > this_year() - 61]
            reasons.append("Public domain in the USA, but in India a book stays protected for 60 years after its author or "
                           f"translator dies: {', '.join(late)}. It stays private here.")
            return _report(url, "gutenberg.org", "Project Gutenberg + contributor death years", lic, b["title"], creator,
                           None, reasons, status="private", kind="book")
    rep = _report(url, "gutenberg.org", "Project Gutenberg + contributor death years", lic, b["title"], creator, None, reasons, kind="book")
    rep["text_url"] = f"https://www.gutenberg.org/cache/epub/{int(book_id)}/pg{int(book_id)}.txt"
    rep["languages"] = b["languages"]
    return rep


def check_ytdlp(url: str, host: str) -> Dict[str, Any]:
    """YouTube, Vimeo and most video sites expose the uploader's licence choice; yt-dlp reads it without downloading."""
    # --ignore-no-formats-error: read the details (licence included) even when no playable format can be chosen
    res = subprocess.run(["yt-dlp", "--skip-download", "--no-playlist", "--ignore-no-formats-error", "-j", url], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    if res.returncode != 0 or not res.stdout.strip():
        tail = (res.stderr.strip().splitlines() or ["no details"])[-1][:200]
        lic = license_info("unknown")
        return _report(url, host, "yt-dlp", lic, reasons=[f"The licence could not be read ({tail})."], status="private")
    d = json.loads(res.stdout.splitlines()[0])
    title, creator = d.get("title") or "", d.get("uploader") or d.get("channel") or ""
    lic = classify(d.get("license") or ("Standard YouTube License" if "youtu" in host else ""))
    reasons, status = [], None
    if lic["adapt"] and REUPLOAD_RE.search(title):
        reasons.append("The uploader marked it Creative Commons, but the title looks like a re-upload of a commercial film or "
                       "show. Only the rights owner can license it, so it stays private.")
        status = "private"
    elif lic["adapt"]:
        reasons.append(f"The uploader ({creator or 'unknown'}) chose {lic['label']} for this video.")
    rep = _report(url, host, f"{'YouTube' if 'youtu' in host else host} licence field", lic, title, creator,
                  _year(d.get("upload_date")), reasons, status=status)
    rep.update({"duration_sec": d.get("duration"), "video_id": d.get("id"), "channel_verified": d.get("channel_is_verified")})
    return rep


def check_url(url: str) -> Dict[str, Any]:
    """Rights report for a link (cached for 30 minutes)."""
    url = (url or "").strip()
    with _lock:
        hit = _cache.get(url)
        if hit and time.time() - hit["_t"] < 1800:
            return {k: v for k, v in hit.items() if k != "_t"}
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("Enter a full http(s) link.")
    host, path = p.netloc.lower().split("@")[-1].split(":")[0], unquote(p.path)
    if blocked_host(host, path):
        rep = _report(url, host, "DRM streaming service", license_info("standard"), status="blocked",
                      reasons=["This is a DRM-protected streaming service. Copying from it breaks its terms and "
                               "anti-circumvention law (India s.65A, US s.1201). Use the Watch Guide, which works inside the official player."])
    elif host.endswith("archive.org") and re.search(r"/(details|download|embed)/([^/?#]+)", path):
        rep = check_archive(re.search(r"/(details|download|embed)/([^/?#]+)", path).group(2), url)
    elif host.endswith("wikimedia.org") or host.endswith("wikipedia.org"):
        m = re.search(r"(File:[^?#]+)", path) or re.search(r"/([^/]+\.(?:webm|ogv|ogg|mp4|mpg|mpeg))$", path, re.I)
        rep = check_commons(m.group(1), url) if m else _report(url, host, "Wikimedia", license_info("unknown"), status="private",
                                                             reasons=["Link to a Commons file page (File:...) to read its licence."])
    elif host.endswith("gutenberg.org") and re.search(r"/(?:ebooks|files|cache/epub)/(\d+)", path):
        rep = check_gutenberg(int(re.search(r"/(?:ebooks|files|cache/epub)/(\d+)", path).group(1)), url)
    else:
        rep = check_ytdlp(url, host)
    with _lock:
        _cache[url] = dict(rep, _t=time.time())
    return rep


# ------------------------------------------------------------------ declarations
BASIS_LABELS = {"own": "I made it or own its rights", "permission": "I have written permission from the rights owner",
                "open": "It is public domain or under an open licence that allows this", "private": "Private viewing only"}


def record_declaration(source: str, basis: str, report: Optional[Dict[str, Any]] = None, terms_version: str = "") -> None:
    """Keeps a local record of what the user declared about a file or link (evidence for a later dispute)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source, "basis": basis, "terms_version": terms_version,
           "auto_status": (report or {}).get("status"), "license": ((report or {}).get("license") or {}).get("label")}
    with open(DECLARATIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def decide(report: Optional[Dict[str, Any]], basis: Optional[str], force_private: bool = False) -> Dict[str, Any]:
    """Final status for a job: a verified licence clears it; a declaration of ownership or permission makes it
    'declared' (full features, recorded); anything else is private."""
    report = dict(report or {"status": "private", "license": license_info("unknown"), "reasons": ["Local file without a licence check."],
                             "caveats": [], "attribution": ""})
    if report.get("status") == "blocked":
        return report
    if force_private:
        report["status"] = "private"
    elif report.get("status") != "cleared" and basis in ("own", "permission", "open"):
        report["status"] = "declared"
        report["reasons"] = list(report.get("reasons") or []) + [f"You declared: {BASIS_LABELS[basis]}. This is recorded, and the Terms make you responsible for it."]
    report["basis"] = basis or ("verified" if report.get("status") == "cleared" else "private")
    return report
