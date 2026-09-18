"""
The library: everything CineCut sells or shares, stored as small recipes instead of finished videos.

A title is a JSON recipe of a few kilobytes, one row in data/library.db (SQLite):
  source     where the original lives: a direct file on the Internet Archive or Wikimedia Commons ("html5"), a YouTube
             video played through the official embed ("youtube"), or a workspace's own upload on this PC ("local",
             streamed only to its members); books and stories need no source video ("none");
  clips      the parts of the original to play, in order, as start and end times in the original;
  narration  per language, the lines spoken over the cut, each tied to the clip it opens;
  script     for books, stories and lecture explainers: the sections with headings, key points and narration, split
             into short pieces for voicing, plus takeaways, glossary and quiz;
  credits    licence, credit line, rights status and the made-with-AI label.
The player rebuilds the summary whenever someone plays it: clips stream straight from the source and each narration
piece is voiced the first time it is needed. Voiced pieces sit in a small cache (CINECUT_LIBRARY_CACHE_MB, default
300 MB) that drops the least recently played files first, so disk use stays flat however large the library grows.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

import httpx

from backend.config import BASE_DIR, TEMP_DIR
from backend.video_engine import rights

LIB_DIR = BASE_DIR / "data" / "library"
CACHE_DIR = TEMP_DIR / "library_audio"
CACHE_MB = int(os.environ.get("CINECUT_LIBRARY_CACHE_MB", "300"))
KINDS = {"movie": "Movies", "series": "Series", "book": "Books", "lecture": "Lectures", "story": "Stories"}
AI_LABEL = "Summary, narration and voice made with AI (CineCut). Not affiliated with the rights holders."
COUNTRIES = {"IN": "India", "US": "United States", "GB": "United Kingdom"}


def _territories(e: Dict[str, Any]) -> List[str]:
    """Where a public title may be shown ("ALL" for openly licensed ones). Copyright is national, so a title that is
    public domain in India is not automatically free in the US or the UK:
      openly licensed (CC)   everywhere, with the credit line (lectures, books); not films, whose CC tags on the Archive
                             are usually added by uploaders who do not own them
      books and stories      India: every author and translator died 60+ years ago; UK: 70+ years ago; US: Project
                             Gutenberg only lists books that are public domain in the US, and other texts count only when
                             the author died before the US cut-off year (so every edition was published before it)
      films and episodes     India: published 60+ years ago; US: published before the US cut-off (95 years); UK film terms
                             run from the deaths of the director, writers and composer, so films are not offered there
    Titles made in the studio are judged under India's rules only."""
    if e.get("territories"):
        return e["territories"]
    r = e.get("rights") or {}
    if e.get("origin") != "catalog":
        return ["IN"]
    if (r.get("license") or "").startswith("CC") and e.get("kind") not in ("movie", "series"):
        return ["ALL"]         # Archive uploaders' CC tags on old films are rarely the rights holder's: films follow the year rules
    b = e.get("build") or {}
    year_now = rights.this_year()
    out = ["IN"]
    if e.get("kind") in ("book", "story"):
        death = r.get("death_year") if r.get("death_year") is not None else b.get("death_year")
        if b.get("gutenberg_id") or (death is not None and death < year_now - 95):
            out.append("US")
        if death is not None and death <= year_now - 71:
            out.append("GB")
    elif (e.get("year") and int(e["year"]) < year_now - 95) or r.get("us_public_domain"):
        out.append("US")               # older than the US term, or Wikidata records why it is free there
    return out


def primary_language(e: Dict[str, Any]) -> Optional[str]:
    langs = e.get("languages") or []
    p = e.get("primary_language") or (e.get("build") or {}).get("language")
    return p if p in langs else (langs[0] if langs else None)


def preferred_language(e: Dict[str, Any], country: Optional[str]) -> Optional[str]:
    """The narration a viewer hears first: Hindi in India, English elsewhere, when the title has it."""
    langs = e.get("languages") or []
    want = "Hindi" if (country or "IN") == "IN" else "English"
    return want if want in langs else primary_language(e)


def needed_languages(e: Dict[str, Any]) -> List[str]:
    """Languages a ready public title still lacks for the countries it is cleared in."""
    t = territories(e)
    need = set()
    if "ALL" in t or "IN" in t:
        need.add("Hindi")
    if "ALL" in t or "US" in t or "GB" in t:
        need.add("English")
    return sorted(need - set(e.get("languages") or []))


_TERR: Dict[str, tuple] = {}             # id -> (recipe version, countries): worked out once per version of a title


def territories(e: Dict[str, Any]) -> List[str]:
    """Where a public title may be shown (see _territories), remembered until the title is saved again."""
    if e.get("territories"):
        return e["territories"]
    tid = e.get("id")
    version = _cache["mtimes"].get(tid)
    hit = _TERR.get(tid)
    if hit and version is not None and hit[0] == version:
        return hit[1]
    out = _territories(e)
    if version is not None:
        _TERR[tid] = (version, out)
    return out


def cleared_in(e: Dict[str, Any], country: Optional[str]) -> bool:
    if not country or e.get("visibility", "public") != "public":
        return True
    t = territories(e)
    return "ALL" in t or country in t
_lock = threading.RLock()
_voice_locks: Dict[str, threading.Lock] = {}
_scan_lock = threading.Lock()
_cache: Dict[str, Any] = {"dir_mtime": None, "scanned": 0.0, "entries": {}, "mtimes": {}}
RESCAN_SECONDS = 5               # how often recipes saved by other processes (a harvest, a script) are picked up
SNAPSHOT = TEMP_DIR / "library_index.pickle"    # the index as one file, so a restart does not read every recipe again
_snap = {"saved": 0.0}


# ------------------------------------------------------------------ storage
_DEV_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au"}
_DEV_SIGNS = {"ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ं": "n", "ँ": "n",
              "ः": "h", "्": "", "़": ""}
_DEV_CONSONANTS = dict(zip("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह",
                           "k kh g gh n ch chh j jh ny t th d dh n t th d dh n p ph b bh m y r l v sh sh s h".split()))


def _latin(text: str) -> str:
    """Devanagari written in Latin letters, well enough for a readable address (प्रेमचंद -> premachand)."""
    out = []
    for i, ch in enumerate(text):
        if ch in _DEV_CONSONANTS:
            nxt = text[i + 1] if i + 1 < len(text) else ""
            out.append(_DEV_CONSONANTS[ch] + ("" if nxt in _DEV_SIGNS else "a"))
        else:
            out.append(_DEV_VOWELS.get(ch) or _DEV_SIGNS.get(ch, ch))
    return "".join(out)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _latin(text or "title").lower()).strip("-")[:40] or "title"


def new_id(title: str) -> str:
    return f"{_slug(title)}-{secrets.token_hex(3)}"


def _path(tid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9\-]{3,60}", tid or ""):
        raise KeyError(tid)
    return LIB_DIR / f"{tid}.json"


LIB_DB = BASE_DIR / "data" / "library.db"
UPSERT = ("INSERT INTO recipes (id, body, updated) VALUES (?, ?, ?) "
          "ON CONFLICT(id) DO UPDATE SET body = excluded.body, updated = excluded.updated")
_db_local = threading.local()


def _db() -> sqlite3.Connection:
    """This thread's connection to the recipe database. WAL mode lets the server and a harvest write at the same time;
    one database file loads in seconds where tens of thousands of small files took minutes on Windows."""
    c = getattr(_db_local, "c", None)
    if c is None:
        LIB_DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(LIB_DB), timeout=60)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("CREATE TABLE IF NOT EXISTS recipes (id TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS recipes_updated ON recipes (updated)")
        c.execute("CREATE TABLE IF NOT EXISTS removed (id TEXT PRIMARY KEY, at REAL NOT NULL)")
        c.commit()
        _db_local.c = c
    return c


def save_many(entries: List[Dict[str, Any]]) -> int:
    """Saves many recipes in one transaction (a harvest, a move)."""
    now = time.time()
    rows = []
    for e in entries:
        _path(e["id"])                       # checks the id
        rows.append((e["id"], json.dumps(e, ensure_ascii=False), now))
    c = _db()
    c.executemany(UPSERT, rows)
    c.executemany("DELETE FROM removed WHERE id = ?", [(r[0],) for r in rows])
    c.commit()
    with _lock:                          # update the index in place: a library of tens of thousands never reloads whole
        for e in entries:
            _cache["entries"][e["id"]] = e
            _cache["mtimes"][e["id"]] = now
    return len(rows)


def save(entry: Dict[str, Any]) -> Dict[str, Any]:
    save_many([entry])
    return entry


def get(tid: str) -> Optional[Dict[str, Any]]:
    try:
        _path(tid)
        row = _db().execute("SELECT body FROM recipes WHERE id = ?", (tid,)).fetchone()
        return json.loads(row[0]) if row else None
    except (KeyError, ValueError, sqlite3.Error):
        return None


def delete(tid: str) -> bool:
    try:
        _path(tid)
    except KeyError:
        return False
    c = _db()
    gone = c.execute("DELETE FROM recipes WHERE id = ?", (tid,)).rowcount
    if gone:
        c.execute("INSERT OR REPLACE INTO removed (id, at) VALUES (?, ?)", (tid, time.time()))
    c.commit()
    with _lock:
        _cache["entries"].pop(tid, None)
        _cache["mtimes"].pop(tid, None)
    return bool(gone)


def _rescan(since: float) -> None:
    """Reads the recipes other processes saved or removed since `since` (0 = everything)."""
    c = _db()
    rows = c.execute("SELECT id, body, updated FROM recipes WHERE updated > ?", (since,)).fetchall()
    gone = [r[0] for r in c.execute("SELECT id FROM removed WHERE at > ?", (since,))]
    fresh = {}
    for tid, body, upd in rows:
        if (_cache["mtimes"].get(tid) or 0) < upd:
            try:
                fresh[tid] = (json.loads(body), upd)
            except ValueError:
                continue
    with _lock:
        for tid, (e, upd) in fresh.items():
            if (_cache["mtimes"].get(tid) or 0) < upd:   # a save() here may already hold a newer version
                _cache["entries"][tid] = e
                _cache["mtimes"][tid] = upd
        for tid in gone:
            if tid not in fresh:
                _cache["entries"].pop(tid, None)
                _cache["mtimes"].pop(tid, None)


def all_entries() -> List[Dict[str, Any]]:
    """Every recipe, from an in-memory index. The first load reads the whole database (a few seconds) and makes
    callers wait; after that a background thread picks up what other processes changed, and nobody waits."""
    if _cache["scanned"] == 0:
        with _scan_lock:
            if _cache["scanned"] == 0:
                start = time.time()
                _rescan(0.0)
                _cache.update(scanned=time.time(), seen=start - 5)
    elif time.time() - _cache["scanned"] > RESCAN_SECONDS and _scan_lock.acquire(blocking=False):
        def run() -> None:
            try:
                start = time.time()
                _rescan(_cache.get("seen", 0.0))
                _cache.update(scanned=time.time(), seen=start - 5)      # a few seconds of overlap, so no commit is missed
            except sqlite3.Error:
                pass
            finally:
                _scan_lock.release()
        threading.Thread(target=run, daemon=True, name="library-rescan").start()
    with _lock:
        return list(_cache["entries"].values())


def card(e: Dict[str, Any]) -> Dict[str, Any]:
    """What a list shows: no source paths, no narration text."""
    src = e.get("source") or {}
    r = e.get("rights") or {}
    b = e.get("build") or {}
    return {"id": e["id"], "kind": e.get("kind"), "title": e.get("title"), "series": e.get("series"), "episode": e.get("episode"),
            "creator": e.get("creator"), "year": e.get("year"), "languages": e.get("languages") or [],
            "minutes": e.get("minutes"), "description": e.get("description") or "", "visibility": e.get("visibility", "public"),
            "status": e.get("status", "ready"), "build_state": b.get("state"), "requested": b.get("requested", 0),
            "backend": src.get("backend"), "has_video": src.get("backend") in ("html5", "youtube", "local"),
            "watch_original": src.get("backend") in ("html5", "youtube"), "page_url": e.get("page_url"),
            "audio_ok": src.get("backend") != "youtube" or bool(e.get("script")),
            "rights": {"status": r.get("status"), "license": r.get("license"), "attribution": r.get("attribution")},
            "hue": int(hashlib.sha1(e["id"].encode()).hexdigest()[:2], 16) * 360 // 256, "created": e.get("created"),
            "territories": territories(e)}


def _sort_key(e: Dict[str, Any]):
    ready = 0 if e.get("status", "ready") == "ready" else 1
    show = (e.get("series") or "") if e.get("kind") == "series" else ""
    return (ready, -((e.get("build") or {}).get("requested", 0)), show, e.get("episode") or 0, -(e.get("priority") or 0), -(e.get("created") or 0))


def _matches(e: Dict[str, Any], kind, q, language, status, wanted, country=None) -> bool:
    if wanted is not None:
        if e["id"] not in wanted:
            return False
    elif e.get("visibility", "public") != "public":
        return False
    elif country and not cleared_in(e, country):
        return False
    if kind and e.get("kind") != kind:
        return False
    if language and language not in (e.get("languages") or []):
        return False
    if status and e.get("status", "ready") != status:
        return False
    return not q or q in " ".join(str(e.get(k) or "") for k in ("title", "series", "creator", "description")).lower()


def search(kind: Optional[str] = None, q: str = "", language: Optional[str] = None, ids: Optional[List[str]] = None,
           status: Optional[str] = None, country: Optional[str] = None) -> List[Dict[str, Any]]:
    """Public titles, or exactly the given ids (a workspace shelf): ready summaries first, then the most asked-for."""
    q = (q or "").strip().lower()
    wanted = set(ids) if ids is not None else None
    return [card(e) for e in sorted((e for e in all_entries() if _matches(e, kind, q, language, status, wanted, country)), key=_sort_key)]


def search_page(kind: Optional[str] = None, q: str = "", language: Optional[str] = None, status: Optional[str] = None,
                offset: int = 0, limit: int = 60, country: Optional[str] = None) -> Dict[str, Any]:
    q = (q or "").strip().lower()
    key = (kind, q, language, status, country)
    hit = _PAGES.get(key)
    if hit and time.time() - hit[0] < LIST_SECONDS:
        hits = hit[1]                     # the same order for every page of "Show more", and no re-sort per request
    else:
        hits = sorted((e for e in all_entries() if _matches(e, kind, q, language, status, None, country)), key=_sort_key)
        if len(_PAGES) > 200:
            _PAGES.clear()
        _PAGES[key] = (time.time(), hits)
    return {"titles": [card(e) for e in hits[offset: offset + limit]], "total": len(hits)}


LIST_SECONDS = 10                      # listings and shelf counts are shared by every viewer for this long
_PAGES: Dict[tuple, tuple] = {}
_STATS: Dict[Any, tuple] = {}


def stats(country: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """Shelf counts as seen from a country, kept for LIST_SECONDS (they are the same for every viewer there)."""
    hit = _STATS.get(country)
    if hit and time.time() - hit[0] < LIST_SECONDS:
        return hit[1]
    out = _stats(country)
    _STATS[country] = (time.time(), out)
    return out


def _stats(country: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {k: {"total": 0, "ready": 0} for k in KINDS}
    for e in all_entries():
        if e.get("visibility", "public") != "public" or e.get("kind") not in out or not cleared_in(e, country):
            continue
        out[e["kind"]]["total"] += 1
        out[e["kind"]]["ready"] += e.get("status", "ready") == "ready"
    return out


# ------------------------------------------------------------------ sources
def _seconds(value: Any) -> float:
    s = str(value or "").strip()
    if ":" in s:
        total = 0.0
        for part in s.split(":"):
            total = total * 60 + float(part or 0)
        return total
    try:
        return float(s)
    except ValueError:
        return 0.0


def _probe_duration(url: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", url],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        return float(r.stdout.strip() or 0)
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def resolve_media(report: Optional[Dict[str, Any]], duration: float = 0.0) -> Optional[Dict[str, Any]]:
    """A public, streamable address for a source whose timings match the analysed file (within 2%)."""
    url = (report or {}).get("url") or ""
    parsed = urlparse(url)
    host, path = parsed.netloc.lower(), unquote(parsed.path)
    if "youtu" in host:
        vid = (report or {}).get("video_id") or (re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url) or [None, None])[1]
        return {"backend": "youtube", "video_id": vid, "duration": duration} if vid else None
    if host.endswith("archive.org"):
        m = re.search(r"/(details|download|embed)/([^/]+)(?:/(.+))?", path)
        if not m:
            return None
        ident, file = m.group(2), m.group(3)
        if m.group(1) == "download" and file:
            return {"backend": "html5", "url": f"https://archive.org/download/{quote(ident)}/{quote(file)}", "duration": duration}
        meta = httpx.get(f"https://archive.org/metadata/{quote(ident)}", headers=rights.UA, timeout=30).json()
        files = [f for f in meta.get("files", []) if str(f.get("name", "")).lower().endswith((".mp4", ".webm", ".ogv"))]
        files.sort(key=lambda f: (0 if f["name"].lower().endswith(".mp4") else 1, int(f.get("size") or 0)))
        for f in files[:6]:
            u = f"https://archive.org/download/{quote(ident)}/{quote(f['name'])}"
            d = _seconds(f.get("length")) or _probe_duration(u)
            if not duration or (d and abs(d - duration) <= max(3.0, duration * 0.02)):
                return {"backend": "html5", "url": u, "duration": d or duration}
        return None
    if "wikimedia.org" in host or "wikipedia.org" in host:
        if host.startswith("upload."):
            return {"backend": "html5", "url": url, "duration": duration}
        m = re.search(r"(File:[^?#]+)", path)
        if not m:
            return None
        r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=rights.UA, timeout=30,
                      params={"action": "query", "format": "json", "titles": m.group(1), "prop": "imageinfo", "iiprop": "url"})
        page = next(iter(((r.json().get("query") or {}).get("pages") or {}).values()), {})
        u = ((page.get("imageinfo") or [{}])[0]).get("url")
        return {"backend": "html5", "url": u, "duration": duration} if u else None
    return None


# ------------------------------------------------------------------ building recipes
def _pieces(text: str, max_chars: int = 280) -> List[str]:
    from backend.video_engine.explainer import _tts_pieces
    return _tts_pieces(text or "", max_chars)


def _rights_block(r: Dict[str, Any]) -> Dict[str, Any]:
    lic = r.get("license") or {}
    return {"status": r.get("status"), "license": lic.get("label") if isinstance(lic, dict) else lic,
            "attribution": r.get("attribution"), "source_url": r.get("url"), "noncommercial": r.get("noncommercial"),
            "share_alike": r.get("share_alike"), "country": r.get("country")}


def from_job(job: Dict[str, Any], kind: Optional[str] = None, series: Optional[str] = None, episode: Optional[int] = None,
             visibility: str = "public", source: Optional[Dict[str, Any]] = None, title: Optional[str] = None) -> Dict[str, Any]:
    """A recipe from an analysed and narrated studio job. The public library takes only cleared sources."""
    r = job.get("rights") or {}
    if visibility == "public" and r.get("status") != "cleared":
        raise ValueError("Only public-domain or openly licensed titles can go in the public library.")
    sel = sorted([s for s in job.get("scenes") or [] if s.get("selected", True)], key=lambda s: s["start"])
    if not sel:
        raise ValueError("The cut has no scenes.")
    src = source or resolve_media(r, (job.get("metadata") or {}).get("duration_sec") or 0.0)
    if not src:
        raise ValueError("No public file of this source could be found to stream from.")
    lang = job.get("language") or "English"
    lines = [{"clip": i, "text": s["bridge_narration"].strip()} for i, s in enumerate(sel)
             if (s.get("bridge_narration") or "").strip() and s.get("narration_source") != "time_skip"]   # "the story moves ahead N minutes" adds nothing here
    name = title or job.get("film_title") or "Untitled"
    entry = {"id": new_id(name), "kind": kind or ("lecture" if job.get("content_mode") == "lecture" else "movie"), "title": name,
             "series": series, "episode": episode, "creator": r.get("creator") or "", "year": r.get("year"),
             "description": job.get("narrative_overview") or ((job.get("essence") or {}).get("essence_theme") or ""),
             "languages": [lang] if lines else [], "source": src,
             "clips": [{"start": round(s["start"], 2), "end": round(s["end"], 2), "title": s.get("title") or ""} for s in sel],
             "narration": {lang: lines} if lines else {}, "rights": _rights_block(r), "visibility": visibility,
             "minutes": round(sum(s["end"] - s["start"] for s in sel) / 60, 1), "ai_label": AI_LABEL, "origin": "studio",
             "created": time.time()}
    return save(entry)


def from_explainer(result: Dict[str, Any], rights_decision: Dict[str, Any], kind: str, visibility: str = "public",
                   source_url: str = "", title: Optional[str] = None) -> Dict[str, Any]:
    """A recipe from an own-words explainer: the script and its pieces, voiced again whenever it is played."""
    if visibility == "public" and rights_decision.get("status") != "cleared":
        raise ValueError("Only public-domain or openly licensed works can go in the public library.")
    lang = result.get("language") or "English"
    sections = [{"heading": s.get("heading") or "", "key_points": s.get("key_points") or [], "narration": s.get("narration") or ""}
                for s in result.get("sections") or []]
    pieces = [{"section": -1, "text": p} for p in _pieces(result.get("intro") or "")]
    pieces += [{"section": i, "text": p} for i, s in enumerate(sections) for p in _pieces(s["narration"])]
    name = title or result.get("title") or "Untitled"
    words = sum(len(p["text"].split()) for p in pieces)
    entry = {"id": new_id(name), "kind": kind, "title": name, "series": None, "episode": None,
             "creator": rights_decision.get("creator") or "", "year": rights_decision.get("year"),
             "description": result.get("thesis") or "", "languages": [lang], "source": {"backend": "none", "url": source_url},
             "script": {"title": result.get("title"), "thesis": result.get("thesis"), "hook": result.get("hook"), "sections": sections,
                        "takeaways": result.get("takeaways") or [], "glossary": result.get("glossary") or [],
                        "quiz": result.get("quiz") or [], "based_on": rights_decision.get("title") or ""},
             "pieces": {lang: pieces}, "rights": _rights_block(rights_decision), "visibility": visibility,
             "minutes": result.get("minutes") or round(words / (190 if lang != "English" else 140), 1), "ai_label": AI_LABEL,
             "origin": "explainer", "created": time.time()}
    return save(entry)


def import_explainer_folder(folder: str, kind: str, rights_decision: Dict[str, Any], language: str) -> Dict[str, Any]:
    """Turns an explainer the studio already made (script.md + notes.json) into a recipe; the MP4 is not needed."""
    d = Path(folder)
    outline = json.loads((d / "notes.json").read_text(encoding="utf-8")).get("outline") or {}
    md = (d / "script.md").read_text(encoding="utf-8")
    blocks = re.split(r"^## (.+)$", md, flags=re.M)
    texts = {}
    for i in range(1, len(blocks) - 1, 2):
        texts[blocks[i].strip()] = blocks[i + 1].split("\n---")[0].strip()
    heads = [s.get("heading", "") for s in outline.get("sections") or []]
    body = [texts.get(h, "") for h in heads]
    recap = [v for k, v in texts.items() if k not in heads and k not in ("Opening", "Glossary", "Check yourself")]
    sections = [{"heading": h, "key_points": s.get("key_points") or [], "narration": t} for h, s, t in zip(heads, outline.get("sections") or [], body)]
    if recap:
        sections.append({"heading": next(k for k, v in texts.items() if v == recap[0]), "key_points": outline.get("takeaways") or [],
                         "narration": recap[0]})
    result = {"language": language, "title": outline.get("title"), "thesis": outline.get("thesis"), "hook": outline.get("hook"),
              "intro": texts.get("Opening", ""), "sections": sections, "takeaways": outline.get("takeaways"),
              "glossary": outline.get("glossary"), "quiz": outline.get("quiz")}
    return from_explainer(result, rights_decision, kind, source_url=rights_decision.get("url", ""))


# ------------------------------------------------------------------ playing
def narration_texts(entry: Dict[str, Any], lang: str) -> List[str]:
    if entry.get("pieces"):
        return [p["text"] for p in (entry["pieces"].get(lang) or [])]
    return [n["text"] for n in ((entry.get("narration") or {}).get(lang) or [])]


def play_plan(entry: Dict[str, Any], lang: Optional[str] = None, original: bool = False) -> Dict[str, Any]:
    """What the player needs, without the source's local path or anything private. original=True (or a title whose
    summary is not made yet) plays the whole original from its site."""
    if original or entry.get("status") == "catalog":
        src = dict(entry.get("source") or {})
        src.pop("path", None)
        r = entry.get("rights") or {}
        plan = {"id": entry["id"], "title": entry.get("title"), "kind": entry.get("kind"), "language": None, "languages": [], "texts": [],
                "creator": entry.get("creator") or "", "source": src, "original": True,
                "credits": {"line": r.get("attribution") or entry.get("title"), "license": r.get("license"),
                            "ai_label": "Original work, shown in full from its source."}}
        if src.get("backend") in ("html5", "youtube", "local"):
            plan.update({"mode": "clips", "clips": [{"start": 0, "end": float(src.get("duration") or 0) or 1e9, "narration": None,
                                                     "title": "Full length"}]})
        else:
            plan["mode"] = "none"
        return plan
    langs = entry.get("languages") or []
    lang = lang if lang in langs else (langs[0] if langs else None)
    src = dict(entry.get("source") or {})
    src.pop("path", None)
    plan: Dict[str, Any] = {"id": entry["id"], "title": entry.get("title"), "kind": entry.get("kind"), "language": lang, "languages": langs,
                            "texts": narration_texts(entry, lang) if lang else [], "creator": entry.get("creator") or "",
                            "source": src, "credits": {"line": (entry.get("rights") or {}).get("attribution") or entry.get("title"),
                                                       "license": (entry.get("rights") or {}).get("license"), "ai_label": entry.get("ai_label", AI_LABEL)}}
    if entry.get("script"):
        sc = (entry.get("scripts") or {}).get(lang) or entry["script"]
        plan.update({"mode": "script", "slides": [{"heading": s["heading"], "points": s.get("key_points") or []} for s in sc["sections"]],
                     "pieces": [{"section": p["section"], "n": i} for i, p in enumerate((entry.get("pieces") or {}).get(lang) or [])],
                     "thesis": sc.get("thesis"), "takeaways": sc.get("takeaways"), "quiz": sc.get("quiz"), "glossary": sc.get("glossary")})
    else:
        lines = ((entry.get("narration") or {}).get(lang) or []) if lang else []
        by_clip = {n["clip"]: i for i, n in enumerate(lines)}
        plan.update({"mode": "clips", "clips": [dict(c, narration=by_clip.get(i)) for i, c in enumerate(entry.get("clips") or [])]})
    return plan


def _voice_for(lang: str) -> str:
    from backend.video_engine.narrator import AVAILABLE_VOICES, LANGUAGE_DEFAULT_VOICE, voice_ready
    for k, v in AVAILABLE_VOICES.items():      # licensed voices first: Sarvam for Indian languages and Indian English
        if v.get("engine") == "sarvam" and v["language"] == lang and voice_ready(k):
            return k
    return LANGUAGE_DEFAULT_VOICE.get(lang, "christopher")


def _trim_cache() -> None:
    files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    limit = CACHE_MB * 1024 * 1024
    for f in files:
        if total <= limit:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def narration_audio(entry: Dict[str, Any], lang: str, idx: int) -> Path:
    """The voiced piece idx of this title in this language, voicing it now if the cache does not have it."""
    from backend.video_engine.narrator import AVAILABLE_VOICES, synthesize_voiceover
    texts = narration_texts(entry, lang)
    if not 0 <= idx < len(texts):
        raise IndexError(idx)
    text, vk = texts[idx], _voice_for(lang)
    name = hashlib.sha1(f"{vk}|{text}".encode("utf-8")).hexdigest()[:24] + ".mp3"
    path = CACHE_DIR / name
    with _lock:
        lk = _voice_locks.setdefault(name, threading.Lock())
    with lk:
        if path.exists() and path.stat().st_size > 0:
            os.utime(path)
            return path
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part.mp3")
        for voice in (vk, AVAILABLE_VOICES.get(vk, {}).get("fallback")):
            if not voice:
                continue
            try:
                synthesize_voiceover(text, voice, str(tmp))
            except Exception:
                continue
            if tmp.exists() and tmp.stat().st_size > 0:
                break
        if not tmp.exists():
            raise RuntimeError("The narration could not be voiced right now.")
        os.replace(tmp, path)
    _trim_cache()
    return path


# Project Gutenberg names some files by edition ("Alice's Adventures in Wonderland HTML Edition", "... Illustrated by
# Arthur Rackham. With a Proem by Austin Dobson"). The shelf shows the work's name; the full name stays in source_title.
_EDITION_TAIL = re.compile(r"[\s.,;:]+(HTML Edition|Illustrated by\b.*|With (a|an) (Proem|Preface|Foreword|Introduction)\b.*)$", re.I)


def display_title(title: str) -> str:
    return _EDITION_TAIL.sub("", title or "").strip() or title


def apply_explainer(entry: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Turns a catalogue entry into a ready title from an explainer result (the entry keeps its id and source)."""
    e = get(entry["id"]) or entry
    lang = result.get("language") or "English"
    sections = [{"heading": s.get("heading") or "", "key_points": s.get("key_points") or [], "narration": s.get("narration") or ""}
                for s in result.get("sections") or []]
    pieces = [{"section": -1, "text": p} for p in _pieces(result.get("intro") or "")]
    pieces += [{"section": i, "text": p} for i, s in enumerate(sections) for p in _pieces(s["narration"])]
    words = sum(len(p["text"].split()) for p in pieces)
    based_on = e.get("source_title") or e.get("title")
    if (e.get("build") or {}).get("nasa_srt") and result.get("title") and not e.get("source_title"):
        # NASA's archive names are often file names ("2021-05-05-Community Leaders V1- OC"): the shelf shows the
        # explainer's own title, and NASA's name stays in the credit line (rights.attribution) and in based_on.
        e["source_title"], e["title"] = e["title"], result["title"]
    elif display_title(e.get("title") or "") != e.get("title") and not e.get("source_title"):
        e["source_title"], e["title"] = e["title"], display_title(e["title"])
    e.update({"script": {"title": result.get("title"), "thesis": result.get("thesis"), "hook": result.get("hook"), "sections": sections,
                         "takeaways": result.get("takeaways") or [], "glossary": result.get("glossary") or [], "quiz": result.get("quiz") or [],
                         "based_on": based_on},
              "pieces": dict(e.get("pieces") or {}, **{lang: pieces}), "languages": sorted(set(e.get("languages") or []) | {lang}),
              "minutes": round(words / (190 if lang != "English" else 140), 1), "description": result.get("thesis") or e.get("description"),
              "status": "ready"})
    e.setdefault("build", {}).update(state="ready", updated=time.time(), originality_pct=result.get("originality_pct"))
    return save(e)


def apply_job(entry: Dict[str, Any], job: Dict[str, Any], lang: str) -> Dict[str, Any]:
    """Turns a catalogue film or episode into a ready title from an analysed, narrated studio job."""
    e = get(entry["id"]) or entry
    sel = sorted([s for s in job.get("scenes") or [] if s.get("selected", True)], key=lambda s: s["start"])
    lines = [{"clip": i, "text": s["bridge_narration"].strip()} for i, s in enumerate(sel)
             if (s.get("bridge_narration") or "").strip() and s.get("narration_source") != "time_skip"]   # "the story moves ahead N minutes" adds nothing here
    if not sel or not lines:
        raise RuntimeError("the cut or its narration came out empty")
    e.update({"clips": [{"start": round(s["start"], 2), "end": round(s["end"], 2), "title": s.get("title") or ""} for s in sel],
              "narration": dict(e.get("narration") or {}, **{lang: lines}), "languages": sorted(set(e.get("languages") or []) | {lang}),
              "minutes": round(sum(s["end"] - s["start"] for s in sel) / 60, 1),
              "description": job.get("narrative_overview") or e.get("description"), "status": "ready"})
    e.setdefault("build", {}).update(state="ready", updated=time.time())
    return save(e)


def cache_stats() -> Dict[str, Any]:
    files = list(CACHE_DIR.glob("*.mp3")) if CACHE_DIR.exists() else []
    try:
        n, size = _db().execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM recipes").fetchone()
    except sqlite3.Error:
        n, size = 0, 0
    return {"titles": n, "recipes_kb": round(size / 1024, 1),
            "audio_cache_mb": round(sum(f.stat().st_size for f in files) / 1048576, 1), "audio_cache_limit_mb": CACHE_MB}
