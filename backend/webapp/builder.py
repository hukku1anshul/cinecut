"""
Library builder: turns catalogue titles into summaries in the background, within the free tiers.

Order: titles people asked for come first; otherwise the builder takes the most popular title of each shelf in turn,
so every shelf grows. One title at a time, sharing the heavy-work limit with the web app.
  books, stories  own-words explainer from the Project Gutenberg text (CINECUT_LIBRARY_LANGUAGE, Hindi by default)
  lectures        own-words explainer from the video's captions (no video download)
  movies, series  download the original, analyse it (captions, or Groq Whisper), write the narration for the cut,
                  keep the recipe (clip times and narration text), delete the download
Rights are checked again before each summary. When the AI providers are resting (per-minute or daily limits) the
builder waits and resumes by itself. Narration audio is not made here; the player voices it on first play.
"""
import os
import re
import shutil
import threading
import time
import traceback
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import BackgroundTasks

from backend.config import TEMP_DIR
from backend.video_engine import gemini_client, llm_usage, rights
from backend.webapp import library as lib
from backend.webapp.jobs import HEAVY

LANGUAGE = os.environ.get("CINECUT_LIBRARY_LANGUAGE", "Hindi")
MINUTES = {"movie": 15, "series": 5, "book": 10, "story": 10, "lecture": 10}
ROTATION = ["book", "story", "lecture", "movie", "series"]
WORK = TEMP_DIR / "library_build"
STATE: Dict[str, Any] = {"running": False, "current": None, "workers": {}, "built": 0, "failed": 0, "skipped": 0, "started": None,
                         "resting_until": 0.0, "log": [], "language": LANGUAGE}
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_turn = [0]
WORKERS = max(1, int(os.environ.get("CINECUT_BUILDER_WORKERS", "3")))   # summary writers that need only the AI providers
TEXT_KINDS = ("book", "story", "lecture")
FILM_KINDS = ("movie", "series")                 # one worker, on the GPU and the heavy-work limit
FILMS = os.environ.get("CINECUT_BUILDER_FILMS", "1") != "0"   # 0: no film worker (it downloads and analyses whole films: heavy on RAM)
RECHECK_SECONDS = 1800                            # how often resting keys are tried again
_pick_lock = threading.Lock()
# YouTube answers caption downloads with HTTP 429 when it limits this address. That says nothing about the video, so
# the lecture is not skipped: every YouTube lecture waits until the time below, and NASA lectures carry on meanwhile.
YT_COOLDOWN_SECONDS = 1800
YT_LIMITED_RE = re.compile(r"HTTP Error 429|Too Many Requests|Sign in to confirm|rate.?limit", re.I)
_yt_until = [0.0]


def _youtube_lecture(e: Dict[str, Any]) -> bool:
    return e.get("kind") == "lecture" and not (e.get("build") or {}).get("nasa_srt")


# Gutenberg often holds one book several times ("... HTML Edition", "... Illustrated by Arthur Rackham"). Summarising it
# again spends the free AI allowance on a copy, so a book or story is one work per title core, author and volume.
_EDITION_TAIL_RE = re.compile(r"\s+(illustrated|with (a|an|the) |being |translated |edited |html edition|an? (new )?edition)\b.*$")
_EDITION_WORD_RE = re.compile(r"\b(html|edition|the|a|an)\b")


def work_key(e: Dict[str, Any]) -> str:
    t = _EDITION_TAIL_RE.sub("", (e.get("title") or "").lower())
    t = " ".join(_EDITION_WORD_RE.sub(" ", re.sub(r"[\"'’‘“”.,:;!?()\[\]{}/\\—–|।॥-]", " ", t)).split())   # punctuation only: Hindi vowel signs stay
    return f"{e.get('kind')}|{t}|{(e.get('creator') or '').lower().split(',')[0].strip()}"
_threads: List[threading.Thread] = []
_last_recheck = [0.0]


def _log(msg: str) -> None:
    STATE["log"] = ([f"{time.strftime('%H:%M')} {msg}"] + STATE["log"])[:60]


def _app():
    import backend.app as appmod
    return appmod


def _mark(entry: Dict[str, Any], **build: Any) -> Dict[str, Any]:
    e = lib.get(entry["id"]) or entry
    e.setdefault("build", {}).update(build, updated=time.time())
    return lib.save(e)


def ai_wait() -> float:
    """Seconds until some text AI provider can be used (0 = now)."""
    waits = []
    for k in gemini_client.all_keys(None):
        w = llm_usage.wait_hint("gemini", [k], gemini_client.models_for(k))
        if w is not None:
            waits.append(w)
    for p, keys in (("groq", llm_usage.env_keys("GROQ")), ("cloudflare", [k for k, _ in llm_usage.cloudflare_accounts()]),
                    ("nvidia", llm_usage.env_keys("NVIDIA")), ("openrouter", llm_usage.env_keys("OPENROUTER"))):
        waits += [llm_usage.free_in(p, k) for k in keys]
    return min(waits) if waits else 0.0


def _groq_audio_ready() -> bool:
    """Room for at least one 10-minute audio part on some Groq key and Whisper model."""
    return any(llm_usage.available("groq_audio", k, m, units=600) for k in llm_usage.env_keys("GROQ")
               for m in ("whisper-large-v3", "whisper-large-v3-turbo"))


def _eligible(e: Dict[str, Any]) -> bool:
    if e.get("status") != "catalog" or e.get("visibility", "public") != "public":
        return False
    b = e.get("build") or {}
    if b.get("state") in ("skipped", "building"):
        return False
    if b.get("state") == "failed" and (b.get("attempts", 0) >= 2 or time.time() - b.get("updated", 0) < 3600):
        return False
    if b.get("deferred") and time.time() - b["deferred"] < 1800:
        return False
    return True


def _pick(kinds=None) -> Optional[Dict[str, Any]]:
    kinds = tuple(kinds or ROTATION)
    cooling = time.time() < _yt_until[0]
    entries = lib.all_entries()
    have = {work_key(e) for e in entries if e.get("kind") in ("book", "story")
            and (e.get("status") != "catalog" or (e.get("build") or {}).get("state") == "building")}
    cands = []
    for e in entries:
        if e.get("kind") not in kinds or not _eligible(e) or (cooling and _youtube_lecture(e)):
            continue
        if e["kind"] in ("book", "story") and work_key(e) in have:
            _mark(e, state="skipped", error="another edition of this work is already in the library")
            continue
        cands.append(e)
    if not cands:
        return None
    asked = [e for e in cands if (e.get("build") or {}).get("requested")]
    if asked:
        return min(asked, key=lambda e: (e["build"].get("requested_at") or 0))
    market = (os.environ.get("CINECUT_MARKET") or "IN").strip().upper()
    rotation = [k for k in ROTATION if k in kinds]
    for _ in rotation:
        kind = rotation[_turn[0] % len(rotation)]
        abroad = (_turn[0] // len(rotation)) % 4 == 3     # one round in four for titles cleared only outside the home market
        _turn[0] += 1
        of_kind = [e for e in cands if e["kind"] == kind]
        if of_kind:
            return max(of_kind, key=lambda e: (lib.cleared_in(e, market) != abroad, e.get("priority") or 0))
    return None


def request(tid: str) -> Dict[str, Any]:
    e = lib.get(tid)
    if not e:
        raise KeyError(tid)
    if e.get("status") != "catalog":
        return {"state": "ready"}
    b = e.setdefault("build", {})
    b["requested"] = b.get("requested", 0) + 1
    b.setdefault("requested_at", time.time())
    if b.get("state") in ("failed", "skipped") and b.get("attempts", 0) < 3:
        b["state"] = "queued"
    lib.save(e)
    waiting = sorted((x for x in lib.all_entries() if x.get("status") == "catalog" and (x.get("build") or {}).get("requested")),
                     key=lambda x: x["build"].get("requested_at") or 0)
    pos = next((i for i, x in enumerate(waiting, 1) if x["id"] == tid), None)
    return {"state": b.get("state"), "position": pos, "builder_running": STATE["running"]}


# ------------------------------------------------------------------ builders
def _lang(e: Dict[str, Any]) -> str:
    """The summary's first language: English for US and UK sources, otherwise CINECUT_LIBRARY_LANGUAGE (Hindi)."""
    return (e.get("build") or {}).get("language") or LANGUAGE


def _minutes(e: Dict[str, Any], text: str) -> float:
    """Summary length: about a third of the original's reading time, between 2 minutes and the shelf's usual length
    (a 500-word guide gets 2 minutes, a novel the full 10)."""
    return round(max(2.0, min(float(MINUTES[e["kind"]]), len(text.split()) / 190 / 3)), 1)


def _explain(e: Dict[str, Any], text: str, kind: str, title: str, author: str) -> None:
    from backend.video_engine import explainer as ex
    work = WORK / e["id"]
    try:
        res = ex.make_explainer({"title": title, "author": author, "text": text, "attribution": (e.get("rights") or {}).get("attribution"),
                                 "origin": e.get("page_url")}, kind, _minutes(e, text), _lang(e), None, work / "w", work / "o", render=False)
        lib.apply_explainer(e, res)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_text_book(e: Dict[str, Any]) -> str:
    from backend.video_engine import explainer as ex
    b = e.get("build") or {}
    if b.get("govuk"):                       # a GOV.UK guide under the Open Government Licence
        from backend.webapp import sources_intl
        text = sources_intl.govuk_text(b["govuk"])
        if len(text.split()) < 400:
            _mark(e, state="skipped", error="guide too short for an explainer")
            return "skip"
        _explain(e, text, "book", e["title"], "GOV.UK")
        return "ok"
    ws = b.get("wikisource")
    if ws:                                   # Hindi classics (India status by the author's death year) or US court opinions
        from backend.webapp import catalog
        api = b.get("wikisource_api") or catalog.WIKISOURCE
        if api == catalog.WIKISOURCE:
            death = (e.get("rights") or {}).get("death_year")
            if death is None or death > rights.this_year() - 61:
                _mark(e, state="skipped", error="the author's death year does not make this public domain in India")
                return "skip"
        text = catalog.wikisource_text(ws, max_parts=150 if (api == catalog.WIKISOURCE or b.get("yt_series")) else 12, api=api)
        if len(text.split()) < b.get("min_words", 1000):      # a single Vedic hymn is short, and that is fine
            _mark(e, state="skipped", error="text too short for an explainer")
            return "skip"
        _explain(e, text, "story" if e["kind"] == "story" else "book", e["title"], e.get("creator") or "")
        return "ok"
    gid = (e.get("build") or {}).get("gutenberg_id")
    rep = rights.check_gutenberg(gid)
    if rep["status"] != "cleared":
        _mark(e, state="skipped", error=" ".join(rep.get("reasons") or ["not public domain in India"])[:300])
        return "skip"
    text = ex.gutenberg_text(rep["text_url"])
    if len(text.split()) < 1500:
        _mark(e, state="skipped", error="text too short for an explainer")
        return "skip"
    _explain(e, text, "story" if e["kind"] == "story" else "book", rep["title"], rep.get("creator") or e.get("creator") or "")
    return "ok"


def build_lecture(e: Dict[str, Any]) -> str:
    import subprocess
    from backend.video_engine import explainer as ex
    from backend.video_engine.subtitle_parser import parse_srt_file
    srt = (e.get("build") or {}).get("nasa_srt")
    if srt:                                  # NASA: a US government work; the summary is written from its caption file
        from backend.webapp import sources_intl
        text = sources_intl.nasa_transcript(srt)
        if len(text.split()) < 500:
            _mark(e, state="skipped", error="captions too short for an explainer")
            return "skip"
        _explain(e, text, "lecture", e["title"], e.get("creator") or "NASA")
        return "ok"
    url = f"https://www.youtube.com/watch?v={e['source']['video_id']}"
    rep = rights.check_url(url)
    if rep["status"] != "cleared":
        _mark(e, state="skipped", error=" ".join(rep.get("reasons") or ["no open licence"])[:300])
        return "skip"
    if time.time() < _yt_until[0]:
        _mark(e, state="queued", deferred=time.time(), error="YouTube is limiting caption downloads; trying again later")
        return "later"
    work = WORK / (e["id"] + "_subs")
    work.mkdir(parents=True, exist_ok=True)
    try:
        run = subprocess.run(["yt-dlp", "--skip-download", "--no-playlist", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,hi.*",
                              "--sleep-subtitles", "2", "--convert-subs", "srt", "-o", str(work / "%(id)s.%(ext)s"), url],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        files = _app().rank_subtitle_files(list(work.glob("*.srt")))
        subs = parse_srt_file(str(files[0])) if files else []
        text = ex.transcript_text(subs)
        if len(text.split()) < 500:
            err = run.stderr or ""
            if not files and YT_LIMITED_RE.search(err):          # refused, not missing: keep it and pause YouTube lectures
                _yt_until[0] = time.time() + YT_COOLDOWN_SECONDS
                _mark(e, state="queued", deferred=time.time(), error="YouTube refused the caption download (HTTP 429); trying again later")
                _log(f"YouTube is limiting caption downloads: YouTube lectures pause for {YT_COOLDOWN_SECONDS // 60} min")
                return "later"
            last = next((ln.strip() for ln in reversed(err.splitlines()) if "ERROR" in ln), "")
            _mark(e, state="skipped", error=("captions too short for an explainer" if files else
                                             "no captions" + (f" ({last[:200]})" if last else "")))
            return "skip"
        e = lib.get(e["id"]) or e
        if "-SA" in (e["rights"].get("license") or ""):      # NPTEL: keep CC BY-SA (YouTube only says CC BY)
            e["rights"]["verified"] = True
        else:
            e["rights"].update({"verified": True, "license": rep["license"]["label"], "attribution": rep.get("attribution") or e["rights"].get("attribution")})
        lib.save(e)
        _explain(e, text, "lecture", e["title"], e.get("creator") or "")
        return "ok"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_film(e: Dict[str, Any]) -> str:
    import asyncio
    app = _app()
    rep = rights.check_url(e["page_url"])
    if rep["status"] != "cleared":
        _mark(e, state="skipped", error=" ".join(rep.get("reasons") or ["not cleared"])[:300])
        return "skip"
    if not _groq_audio_ready():
        _mark(e, state="queued", deferred=time.time())
        return "defer"
    work = WORK / e["id"]
    work.mkdir(parents=True, exist_ok=True)
    path = work / "source.mp4"
    job_id = None
    try:
        with httpx.stream("GET", e["source"]["url"], headers=rights.UA, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            size = 0
            with open(path, "wb") as f:
                for chunk in r.iter_bytes(8 * 1024 * 1024):
                    size += len(chunk)
                    if size > 3 * 1024 ** 3:
                        raise RuntimeError("source file larger than 3 GB")
                    f.write(chunk)
        rights.RIGHTS_BY_PATH[app._path_key(str(path))] = {"report": rep, "decision": rights.decide(rep, None), "downloaded": False}
        app._web_terms.ok = True
        bt = BackgroundTasks()
        res = app.analyze_video(app.AnalyzeRequest(video_path=str(path), target_minutes=MINUTES[e["kind"]], content_mode="movie",
                                                   film_title=e["title"], use_vision=False), bt)
        job_id = res["job_id"]
        for t in bt.tasks:
            t.func(*t.args, **t.kwargs)
        job = app.JOBS[job_id]
        if job["status"] != "ready":
            raise RuntimeError(job.get("error") or "analysis failed")
        # narration text only: the library voices it when someone plays the title
        app.generate_gap_narrations(job["scenes"], job.get("subtitles") or [], app.film_title(job), LANGUAGE, app.effective_key(),
                                    app.story_milestones(job), "movie", None, True, job["metadata"]["duration_sec"],
                                    names=app.beat_character_names(job.get("essence") or {}), visuals=job.get("visuals"))
        lib.apply_job(e, job, LANGUAGE)
        return "ok"
    finally:
        if job_id:
            _app().JOBS.pop(job_id, None)
            shutil.rmtree(TEMP_DIR / job_id, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def translate_one(e: Optional[Dict[str, Any]] = None) -> bool:
    """Adds one missing language to one ready title (English for US and UK viewers, Hindi for India), asked-for and
    popular titles first. True when a translation was saved."""
    from backend.webapp import translate
    if e is None:
        cands = [x for x in lib.all_entries() if x.get("status") == "ready" and x.get("visibility", "public") == "public"
                 and lib.needed_languages(x) and (x.get("build") or {}).get("translate_failed", 0) < 2]
        if not cands:
            return False
        e = max(cands, key=lambda x: ((x.get("build") or {}).get("requested", 0), x.get("priority") or 0))
    for lang in lib.needed_languages(e)[:1]:
        try:
            translate.translate_title(e, lang)
            _log(f"translated: {e['title'][:50]} -> {lang}")
            return True
        except Exception as ex:
            b = (lib.get(e["id"]) or e).get("build") or {}
            _mark(e, translate_failed=b.get("translate_failed", 0) + 1, translate_error=str(ex)[:200])
            _log(f"translation failed: {e['title'][:50]} ({str(ex)[:80]})")
    return False


BUILDERS = {"book": build_text_book, "story": build_text_book, "lecture": build_lecture, "movie": build_film, "series": build_film}


# ------------------------------------------------------------------ loop
def _recheck_keys() -> None:
    """Every half hour, one tiny request to each resting key and model; the ones that answer are used again at once."""
    if time.time() - _last_recheck[0] < RECHECK_SECONDS:
        return
    _last_recheck[0] = time.time()
    try:
        from backend.video_engine import key_check
        c = key_check.recheck_resting()
        _log(f"keys re-checked: {c['lifted']} back in use, {c['still']} still limited, {c['gone']} models gone")
    except Exception as ex:
        _log(f"key re-check failed: {str(ex)[:80]}")


def _worker(n: int, kinds, heavy: bool) -> None:
    """One builder: films on the GPU (heavy=True), or summaries of books, stories and lectures that need only the AI
    providers; several of those run side by side and share the keys."""
    turns = 0
    while not _stop.is_set():
        try:
            if n == 1:
                _recheck_keys()
            wait = ai_wait()
            if wait > 120:
                STATE["resting_until"] = time.time() + wait
                if n <= 1:
                    _log(f"AI providers resting for about {int(wait // 60)} min; waiting")
                _stop.wait(min(wait, 900))
                continue
            turns += 1
            if not heavy and turns % 3 == 0 and translate_one():   # every third turn, a missing language for a finished title
                _stop.wait(2)
                continue
            with _pick_lock:                      # two workers never take the same title
                e = _pick(kinds)
                if e:
                    _mark(e, state="building")
            if not e:
                _stop.wait(60)
                continue
            with (HEAVY if heavy else nullcontext()):
                job = {"id": e["id"], "title": e["title"], "kind": e["kind"], "since": time.time(), "worker": n}
                STATE["workers"][n] = job
                STATE["current"] = job
                try:
                    result = BUILDERS[e["kind"]](e)
                    if result == "ok":
                        STATE["built"] += 1
                        _log(f"ready: {e['kind']} · {e['title'][:60]}")
                        fresh = lib.get(e["id"])
                        if fresh and lib.needed_languages(fresh):
                            translate_one(fresh)
                    elif result == "skip":
                        STATE["skipped"] += 1
                        _log(f"skipped: {e['title'][:60]} ({(lib.get(e['id']) or {}).get('build', {}).get('error', '')[:80]})")
                    else:
                        _log(f"later: {e['title'][:60]} ({((lib.get(e['id']) or {}).get('build') or {}).get('error') or 'waiting for free transcription'})"[:160])
                except Exception as ex:
                    STATE["failed"] += 1
                    b = (lib.get(e["id"]) or e).get("build") or {}
                    _mark(e, state="failed", attempts=b.get("attempts", 0) + 1, error=str(ex)[:300])
                    _log(f"failed: {e['title'][:60]} ({str(ex)[:100]})")
                    traceback.print_exc()
                finally:
                    STATE["workers"].pop(n, None)
            _stop.wait(2)
        except Exception as ex:            # a builder must never die on an unexpected error
            _log(f"builder error: {str(ex)[:120]}")
            _stop.wait(60)


def _boot() -> None:
    for e in lib.all_entries():                 # a crash mid-build leaves titles marked "building"
        if (e.get("build") or {}).get("state") == "building":
            _mark(e, state="queued")
    _log(f"builder started: {WORKERS} summary workers and {1 if FILMS else 0} film worker (summaries in {LANGUAGE})")
    threads = [threading.Thread(target=_worker, args=(0, FILM_KINDS, True), daemon=True, name="library-film")] if FILMS else []
    threads += [threading.Thread(target=_worker, args=(i, TEXT_KINDS, False), daemon=True, name=f"library-text-{i}")
                for i in range(1, WORKERS + 1)]
    _threads.extend(threads)
    for t in threads:
        t.start()


def start() -> Dict[str, Any]:
    if any(t.is_alive() for t in _threads):
        return dict(STATE, library=None)
    _stop.clear()
    _threads.clear()
    STATE.update(running=True, started=time.time(), workers={})
    boot = threading.Thread(target=_boot, daemon=True, name="library-builder")
    _threads.append(boot)
    boot.start()
    return dict(STATE, library=None)      # no library count here: at server start that would read the whole index


def stop() -> Dict[str, Any]:
    _stop.set()
    return status()


def status() -> Dict[str, Any]:
    return dict(STATE, library=lib.stats(), resting_for_min=max(0, int((STATE["resting_until"] - time.time()) // 60)))
