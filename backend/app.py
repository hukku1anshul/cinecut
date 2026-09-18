import asyncio
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, quote

from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse, Response, HTMLResponse, RedirectResponse

from backend.config import (BASE_DIR, TEMP_DIR, OUTPUT_DIR, FRONTEND_DIR, FFMPEG_BIN, DEFAULT_TARGET_MINUTES, PRESETS,
                            SERVER_PORT, LAN_MODE, DEFAULT_GEMINI_MODEL)
from backend.video_engine.probe import (get_video_metadata, extract_subtitles_to_srt, format_seconds,
                                        check_nvenc_support, get_gpu_name, get_media_duration, extract_thumbnail_frame)
from backend.video_engine.subtitle_parser import (parse_srt_file, group_dialogue_into_scenes,
                                                  score_dialogue_significance, format_script_for_llm,
                                                  extract_character_roster)
from backend.video_engine.audio_analyzer import analyze_audio_track, EnergyIndex
from backend.video_engine.scene_detector import detect_scene_cuts, generate_scene_thumbnails, cut_density_score
from backend.video_engine.ai_director import generate_ai_narrative_cut
from backend.video_engine.summarizer import compute_local_heuristic_summary, refine_scene_boundaries, fit_and_snap
from backend.video_engine.renderer import render_summary_video
from backend.video_engine.narrator import (synthesize_voiceover, synthesize_voiceover_async,
                                           generate_ai_bridge_narrations, AVAILABLE_VOICES, LANGUAGE_DEFAULT_VOICE,
                                           default_voice_for_language, voice_for_text, tts_cache_path,
                                           pick_scene_narration)
from backend.video_engine.nle_exporter import export_cmx3600_edl, export_premiere_fcpxml
from backend.video_engine.subtitles_and_chapters import generate_synced_srt
from backend.video_engine.shorts_generator import extract_viral_shorts, bundle_shorts_into_zip
from backend.video_engine.fair_use_shield import enforce_fair_use_clipping, calculate_fair_use_score
from backend.video_engine.season_batch import scan_season_folder, assemble_season_recap
from backend.video_engine.watchfolder import WatchfolderDaemon, get_watchfolder_daemon, set_watchfolder_daemon
from backend.video_engine.social_publisher import (generate_social_publishing_pack, dispatch_publishing_webhook,
                                                   generate_youtube_upload_pack)
from backend.video_engine.legal_armor import generate_dmca_counter_notification, generate_fair_use_slate_video
from backend.video_engine.speech_transcriber import (is_whisper_available, transcribe_video_offline,
                                                     groq_transcription_available, transcribe_with_groq)
from backend.video_engine import llm_usage
from backend.video_engine.essence_engine import extract_film_canonical_essence, clean_movie_title
from backend.video_engine.lecture_engine import build_lecture_cut, gemini_lecture_cut, generate_study_notes
from backend.video_engine.watch_guide import build_watch_guide, guide_to_text
from backend.video_engine import llm_client
from backend.video_engine.story_beats import ai_story_beats, merge_supporting_beats
from backend.video_engine.summarizer import FILTER_FOR_PRESET, FILTER_LABELS as BEAT_FILTER_LABELS, beat_tags
from backend.video_engine.essence_engine import match_database_entry
from backend.video_engine.youtube_cut import youtube_id_from_path, build_youtube_cut_html, timestamp_links
from backend.video_engine.gap_narrator import generate_gap_narrations
from backend.video_engine.flavor_detector import detect_flavor_segments, FLAVOR_FOR_PRESET, FLAVOR_LABELS, ANY_TAG
from backend.video_engine.lecture_engine import enrich_topics_with_ai
from backend.video_engine.trailer import make_trailer
from backend.video_engine import visual_scenes, quality as quality_mod, ml_detectors, tts_engines
from backend.video_engine.narrator import voice_ready, engine_of
from backend.video_engine import rights, library, privacy, explainer as explainer_mod
from backend.video_engine.endcard import append_end_card, credit_for

APP_VERSION = "11.0.0"
app = FastAPI(title="CineCut AI Studio", version=APP_VERSION)

JOBS: Dict[str, Dict[str, Any]] = {}
SEASON_BATCH_JOBS: Dict[str, Dict[str, Any]] = {}
TASKS: Dict[str, Dict[str, Any]] = {}
WATCH_GUIDE_CACHE: Dict[tuple, Dict[str, Any]] = {}
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,48}$")
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts"}
EXPLAINERS: Dict[str, Dict[str, Any]] = {}
TERMS_VERSION = "2026-09-13"
TERMS_FILE = BASE_DIR / "data" / "terms_acceptance.json"

# =============================================================================
# SECURITY
# The server is a local tool with access to your disk, so it only answers requests addressed to this
# machine (blocks DNS-rebinding), rejects cross-site POSTs from other web pages, and in LAN mode lets
# other devices reach only the TV player.
# =============================================================================
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
STREAMING_ORIGIN_RE = re.compile(
    r"^https://([a-z0-9-]+\.)*(netflix\.com|primevideo\.com|amazon\.(com|in|co\.uk|de|fr|es|it|co\.jp|ca|com\.au))$")
TV_PUBLIC_PREFIXES = ("/tv", "/api/tv/", "/styles.css", "/app", "/api/app/", "/api/v1/", "/terms")   # also the web app
PUBLIC_HOSTS = {h.strip().lower() for h in (os.environ.get("CINECUT_PUBLIC_HOSTS", "") + "," +
                                             os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")).split(",") if h.strip()}
# Hosted on the internet (Render sets CINECUT_PUBLIC=1): only the web app is served. The studio endpoints read paths on
# this machine's disk, delete library titles and open folders, so on a public server they must not exist at all.
PUBLIC_MODE = os.environ.get("CINECUT_PUBLIC") == "1"
PUBLIC_PATHS = ("/app", "/api/app/", "/api/v1/", "/terms", "/api/health")
_LAN_IPS: Optional[set] = None


def _primary_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def _lan_ips() -> set:
    global _LAN_IPS
    if _LAN_IPS is None:
        ips = {_primary_lan_ip()}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ips.add(info[4][0])
        except Exception:
            pass
        _LAN_IPS = {ip.lower() for ip in ips}
    return _LAN_IPS


def _allowed_hosts() -> set:
    return LOOPBACK_NAMES | (_lan_ips() if LAN_MODE else set()) | PUBLIC_HOSTS


def _host_only(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


@app.middleware("http")
async def security_guard(request: Request, call_next):
    path = request.url.path
    origin = request.headers.get("origin")
    if PUBLIC_MODE:                       # the Host check guards a local tool against DNS rebinding; a public server has none to guard
        if path in ("", "/"):
            return RedirectResponse("/app/", status_code=302)
        if path == "/api/health":         # the studio's health report names the GPU, voices and AI providers: not for the internet
            return Response('{"status":"ok"}', media_type="application/json")
        if not path.startswith(PUBLIC_PATHS):
            return PlainTextResponse("Not found.", status_code=404)
        if origin and request.method not in ("GET", "HEAD") and (urlparse(origin).hostname or "").lower() not in _allowed_hosts():
            return PlainTextResponse("Blocked: cross-site request.", status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response
    if _host_only(request.headers.get("host", "")) not in _allowed_hosts():
        return PlainTextResponse("Blocked: unexpected Host header.", status_code=403)

    # The watch guide may be read by the CineCut HUD running on Netflix / Prime Video pages
    if path.startswith("/api/watchguide") and origin and STREAMING_ORIGIN_RE.match(origin):
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET", "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Private-Network": "true"}
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)
        if request.method != "GET":
            return PlainTextResponse("Blocked.", status_code=403)
        response = await call_next(request)
        response.headers.update(cors)
        return response

    if origin and request.method not in ("GET", "HEAD"):
        if (urlparse(origin).hostname or "").lower() not in _allowed_hosts():
            return PlainTextResponse("Blocked: cross-site request.", status_code=403)

    client_ip = request.client.host if request.client else ""
    if LAN_MODE and not (client_ip.startswith("127.") or client_ip in ("::1", "localhost")):
        if not path.startswith(TV_PUBLIC_PREFIXES):
            return PlainTextResponse("Only the TV player is shared on the network.", status_code=403)

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


# =============================================================================
# HELPERS
# =============================================================================
def clean_user_path(p: Optional[str]) -> str:
    return (p or "").strip().strip('"').strip("'").strip()


def effective_key(*keys: Optional[str]) -> Optional[str]:
    """The Gemini key from the request/job, else GEMINI_API_KEY / GOOGLE_API_KEY from the environment."""
    for k in keys:
        if k:
            return k
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def get_job(job_id: str) -> Dict[str, Any]:
    if not ID_RE.match(job_id or "") or job_id not in JOBS:
        if job_id in privacy.DELETED:
            raise HTTPException(status_code=410, detail="This private summary has been deleted, as promised. Analyze the video again to see it.")
        raise HTTPException(status_code=404, detail="Job not found. (Jobs are kept in memory; re-analyze after a restart.)")
    job = JOBS[job_id]
    _touch_private(job_id, job)
    return job


def _touch_private(item_id: str, item: Dict[str, Any]) -> None:
    """Opening a private summary restarts its deletion timer (written at most every 30 s)."""
    if item.get("private") and time.time() - item.get("_touched", 0) > 30:
        item["_touched"] = time.time()
        privacy.touch(item_id)


def _path_key(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def terms_accepted() -> bool:
    try:
        return json.loads(TERMS_FILE.read_text(encoding="utf-8")).get("version") == TERMS_VERSION
    except (OSError, ValueError):
        return False


_web_terms = threading.local()        # web app work: the user accepted the Terms at sign-up


def require_terms() -> None:
    if getattr(_web_terms, "ok", False):
        return
    if not terms_accepted():
        raise HTTPException(status_code=428, detail="Please read and accept the Terms of Use first (they open when CineCut starts).")


def no_export(item: Dict[str, Any]) -> None:
    if item.get("private"):
        raise HTTPException(status_code=403, detail="Private viewing: downloads and exports are off because this material has no "
                                                    "verified licence. Watch it here; it is deleted afterwards.")


def safe_child(base: Path, *parts: str) -> Path:
    """Joins user-supplied path parts to `base`, refusing anything that could escape it."""
    for p in parts:
        if not p or p in (".", "..") or any(c in p for c in ("/", "\\", ":", "\x00")):
            raise HTTPException(status_code=404, detail="Not found.")
    base_r = base.resolve()
    target = base_r.joinpath(*parts).resolve()
    if target != base_r and base_r not in target.parents:
        raise HTTPException(status_code=404, detail="Not found.")
    return target


def selected_signature(scenes: List[Dict[str, Any]]) -> str:
    return hashlib.sha1(repr([(s["start"], s["end"]) for s in scenes if s.get("selected", True)]).encode()).hexdigest()


def update_totals(job: Dict[str, Any]) -> None:
    total = sum(s["duration"] for s in job.get("scenes", []) if s.get("selected", True))
    job["total_selected_sec"] = round(total, 2)
    job["total_selected_formatted"] = format_seconds(total)
    try:
        job["quality"] = quality_mod.score_cut(job)
    except Exception as e:
        job["quality"] = {"score": None, "parts": {}, "issues": [f"Quality score unavailable: {e}"]}


def display_title(raw: str) -> str:
    """'Hindi Medium Full Movie: Irrfan Khan, ... | Comedy [id].mp4' -> 'Hindi Medium' (keeps words like 'Hindi')."""
    t = str(raw or "")
    if Path(t).suffix.lower() in VIDEO_EXTS:
        t = Path(t).stem
    t = re.split("[|｜]", t)[0]
    t = re.sub("[[][^]]*[]]", " ", t)
    m = re.search("(?i)(full (movie|film)|official (trailer|movie))", t)
    if m and m.start() > 2:
        t = t[:m.start()]
    return " ".join(t.replace("_", " ").split()).strip(" :-") or "the film"


def film_title(job: Dict[str, Any]) -> str:
    ess = job.get("essence") or {}
    if ess.get("movie_title") and not ess.get("is_generic"):
        return ess["movie_title"]
    return display_title(job.get("film_title") or Path(job["video_path"]).stem)


def story_blueprint(job: Dict[str, Any], subs: List[Dict[str, Any]], duration: float,
                    api_key: Optional[str]) -> Dict[str, Any]:
    """Story beats in order of trust: hand-made database, the film's own dialogue (AI),
    the AI's knowledge of the film, and only then the generic three-act template."""
    title = display_title(job.get("film_title") or Path(job["video_path"]).stem)
    if match_database_entry(title):
        ess = extract_film_canonical_essence(title, duration, api_key)
        if subs and not ess.get("is_generic"):
            extra = ai_story_beats(title, subs, duration, job["video_path"], api_key, visuals=job.get("visuals"))
            if extra:
                ess = merge_supporting_beats(ess, extra)
        return ess
    if subs:
        beats = ai_story_beats(title, subs, duration, job["video_path"], api_key, visuals=job.get("visuals"))
        if beats:
            return beats
    return extract_film_canonical_essence(title, duration, api_key)


def story_milestones(job: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Film-specific story beats only (the generic three-act template says nothing about this film)."""
    ess = job.get("essence") or {}
    return None if ess.get("is_generic") else ess.get("milestones")


ANALYSIS_CACHE_VERSION = "a3"


def detect_shots_best(path: str, threshold: float, duration: float) -> List[float]:
    """TransNetV2 on the GPU when PyTorch is installed (catches fades and dissolves), otherwise FFmpeg."""
    if ml_detectors.available():
        cuts = ml_detectors.detect_shots(path, duration)
        if cuts and len(cuts) > 10:
            return cuts
    return detect_scene_cuts(path, threshold, 20000, None, 0.5, duration)


def dedupe_segments(segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for g in sorted(segs, key=lambda x: -x["score"]):
        if all(g["end"] <= o["start"] or g["start"] >= o["end"] for o in out):
            out.append(g)
    return out


def load_words(srt_path: Path) -> List[Dict[str, Any]]:
    import json as _json
    wp = Path(srt_path).with_suffix(".words.json")
    try:
        return _json.loads(wp.read_text(encoding="utf-8")) if wp.exists() else []
    except Exception:
        return []


def ensure_one_voice(scenes: List[Dict[str, Any]], vo_dir: Path, engine_label: str, azure_key=None, azure_region=None) -> int:
    """If an expressive voice ran out of free quota half-way, re-voices those lines with its Edge fallback so the whole
    narration uses one voice. Returns how many lines were re-voiced."""
    voiced = [s for s in scenes if s.get("voiceover_audio_path") and s.get("voice")]
    engines = {engine_of(s["voiceover_audio_path"]) for s in voiced}
    if len(engines) <= 1:
        return 0
    redone = 0
    for s in voiced:
        info = AVAILABLE_VOICES.get(s["voice"], {})
        if info.get("engine", "edge") == "edge" or engine_of(s["voiceover_audio_path"]) == "edge":
            continue
        fb = info.get("fallback", "christopher")
        text = s.get("voiceover_text") or s.get("bridge_narration")
        path = tts_cache_path(vo_dir, text, fb, engine=engine_label)
        if not path.exists():
            synthesize_voiceover(text, fb, str(path), azure_key=azure_key, azure_region=azure_region)
        s.update({"voiceover_audio_path": str(path), "voice": fb})
        redone += 1
    return redone


def cached_analysis(path: str, threshold: float, duration: float):
    """Audio + shot analysis, cached on disk by file size/date so re-opening a video is instant."""
    import json as _json
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{threshold}|{ANALYSIS_CACHE_VERSION}".encode()).hexdigest()[:20]
    cache = TEMP_DIR / "analysis_cache" / f"{key}.json"
    if cache.exists():
        try:
            d = _json.loads(cache.read_text(encoding="utf-8"))
            return ({"silences": [tuple(x) for x in d["silences"]], "energy": d["energy"], "audio_tags": d.get("audio_tags")},
                    d["shot_cuts"], True)
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(analyze_audio_track, path, -30, 0.4, 1.0, max(600.0, duration * 0.5))
        fs = pool.submit(detect_shots_best, path, threshold, duration)
        audio, cuts = fa.result(), fs.result()
    audio["audio_tags"] = ml_detectors.tag_audio(path, duration) if ml_detectors.available() else None
    if audio["energy"] and len(cuts) > 1 and privacy.cache_allowed():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(_json.dumps({"silences": audio["silences"], "energy": audio["energy"], "shot_cuts": cuts,
                                      "audio_tags": audio["audio_tags"]}), encoding="utf-8")
    return audio, cuts, False


def rank_subtitle_files(paths: List[Path]) -> List[Path]:
    """Best first: human-made (punctuated) tracks over auto-captions, English before Hindi, then longest."""
    scored = []
    for p in paths:
        subs = parse_srt_file(str(p))
        if not subs:
            continue
        punct = sum(1 for x in subs if any(c in x["text"] for c in ".?!,")) / len(subs)
        name = Path(p).name.lower()
        english = ".en" in name
        scored.append((round(punct, 1) + (0.2 if english else 0) - (0.2 if "-orig" in name else 0), len(subs), p))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [t[2] for t in scored]


def find_sidecar_subtitles(video_path: str) -> Optional[str]:
    p = Path(video_path)
    for suffix in (".srt", ".en.srt", ".eng.srt", ".hi.srt", ".vtt", ".en.vtt"):
        c = p.with_name(p.stem + suffix)
        if c.exists():
            return str(c)
    matches = sorted(glob.glob(os.path.join(glob.escape(str(p.parent)), glob.escape(p.stem) + ".*.srt")))
    ranked = rank_subtitle_files([Path(m) for m in matches])
    return str(ranked[0]) if ranked else None


def load_subtitles(video_path: str, meta: Dict[str, Any], external: Optional[str], job_dir: Path):
    ext = clean_user_path(external)
    if ext and os.path.isfile(ext):
        subs = parse_srt_file(ext)
        if subs:
            return subs, "external file"
    sidecar = find_sidecar_subtitles(video_path)
    if sidecar:
        subs = parse_srt_file(sidecar)
        if subs:
            return subs, f"sidecar file ({Path(sidecar).name})"
    if meta.get("has_subtitles"):
        out = job_dir / "extracted_subtitles.srt"
        if extract_subtitles_to_srt(video_path, str(out)):
            subs = parse_srt_file(str(out))
            if subs:
                return subs, "embedded track"
    return [], None


def build_candidates(subtitles, energy_index: EnergyIndex, shot_cuts, video_dur: float,
                     target_character: Optional[str] = None) -> List[Dict[str, Any]]:
    """Candidate scenes with real audio-energy and editing-pace scores (they used to be fixed at 50)."""
    cands = []
    if subtitles:
        for g in group_dialogue_into_scenes(subtitles):
            cands.append({"start": g["start"], "end": g["end"], "duration": g["duration"], "dialogue": g["dialogue"],
                          "dialogue_score": score_dialogue_significance(g["dialogue"], g["duration"], target_character),
                          "audio_score": energy_index.percentile(g["start"], g["end"]),
                          "motion_score": cut_density_score(shot_cuts, g["start"], g["end"], video_dur)})
    else:
        t, step = 0.0, 40.0
        while t + 10.0 < video_dur:
            e = min(video_dur, t + step)
            cands.append({"start": t, "end": e, "duration": e - t, "dialogue": "", "synthetic": True, "dialogue_score": 0.0,
                          "audio_score": energy_index.percentile(t, e),
                          "motion_score": cut_density_score(shot_cuts, t, e, video_dur)})
            t += step
    return cands


DETECTOR_FILTERS = ("song", "action", "comedy", "emotional")
ALL_FILTERS = ("song", "action", "comedy", "emotional", "romance", "hero", "villain")


def normalize_filters(filters: Optional[List[str]], preset: str) -> List[str]:
    picked = [f for f in (filters or []) if f in ALL_FILTERS]
    if not picked:
        picked = list(FILTER_FOR_PRESET.get(preset, []))
    return list(dict.fromkeys(picked))


def focus_names_for(essence: Dict[str, Any], filters: List[str], target_character: Optional[str]) -> List[str]:
    names = [target_character.strip()] if target_character and target_character.strip() else []
    roles = {"hero": ("hero", "lead"), "villain": ("villain", "antagonist")}
    for f, wanted in roles.items():
        if f in filters:
            names += [c["name"] for c in essence.get("main_characters", []) if c.get("role") in wanted]
    return list(dict.fromkeys(n for n in names if n))


def beat_character_names(essence: Dict[str, Any]) -> List[str]:
    counts: Dict[str, int] = {}
    for c in essence.get("main_characters", []):
        counts[c["name"]] = counts.get(c["name"], 0) + 5
    for m in essence.get("milestones", []):
        for c in m.get("characters") or []:
            counts[c] = counts.get(c, 0) + 1
    return [n for n, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:20]


def round_robin(per_filter: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Best segment of each chosen filter in turn, so every filter gets its share of the time."""
    out, lists = [], [list(v) for v in per_filter.values() if v]
    while lists:
        for lst in list(lists):
            out.append(lst.pop(0))
            if not lst:
                lists.remove(lst)
    return out


def transcript_cache_path(video_path: str) -> Path:
    import hashlib
    st = os.stat(video_path)
    sig = f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}"
    return TEMP_DIR / "transcripts" / f"auto_{hashlib.sha1(sig.encode()).hexdigest()[:20]}.srt"


def fill_to_target(primary: List[Dict[str, Any]], extra: List[Dict[str, Any]], target: float) -> List[Dict[str, Any]]:
    """Keeps the AI Director's scenes and adds story-engine scenes, most important first, that do not overlap
    them, until the requested length is reached."""
    chosen = [dict(x) for x in primary]
    total = sum(x["end"] - x["start"] for x in chosen)
    for sc in sorted(extra, key=lambda x: -float(x.get("importance") or 0)):
        if total >= target * 0.98:
            break
        if any(sc["start"] < c["end"] + 1.0 and sc["end"] > c["start"] - 1.0 for c in chosen):
            continue
        length = sc["end"] - sc["start"]
        if total + length > target * 1.03:
            room = target * 1.02 - total
            if room < 20.0:
                continue
            sc = dict(sc, end=sc["start"] + room, duration=round(room, 2), end_formatted=format_seconds(sc["start"] + room))
        chosen.append(dict(sc))
        total += sc["end"] - sc["start"]
    return sorted(chosen, key=lambda x: x["start"])


def curate_scenes(job: Dict[str, Any], target_minutes: float, preset: str, spoiler_mode: str,
                  target_character: Optional[str], enforce_fair_use: bool, max_clip_sec: float,
                  use_ai: bool, api_key: Optional[str], filters: Optional[List[str]] = None,
                  filter_share: float = 0.4) -> List[Dict[str, Any]]:
    video_dur = job["metadata"]["duration_sec"]
    subs = job.get("subtitles") or []
    shot_cuts = job.get("shot_cuts") or []
    silences = job.get("silences") or []
    energy_index = EnergyIndex(job.get("energy") or [])
    target_sec = float(target_minutes) * 60.0
    job["warnings"] = [w for w in job.get("warnings", []) if not w.startswith("AI:")]
    scenes: Optional[List[Dict[str, Any]]] = None

    if job.get("content_mode") == "lecture":
        if use_ai and api_key and len(subs) > 10:
            try:
                res = gemini_lecture_cut(format_script_for_llm(subs), int(target_minutes), video_dur, api_key)
                scenes = refine_scene_boundaries(res["scenes"], shot_cuts, silences, video_dur) or None
                job["narrative_overview"] = res.get("overview", "")
            except Exception as e:
                job["warnings"].append(f"AI: Gemini lecture analysis failed ({e}). Used the offline lecture engine.")
        if not scenes:
            scenes = build_lecture_cut(subs, shot_cuts, silences, video_dur, target_sec, job.get("energy"))
        if scenes and subs:
            lecture_cues = [(float(x["start"]), float(x["end"])) for x in subs if ANY_TAG.sub(" ", x.get("text", "")).strip()]
            scenes = fit_and_snap(scenes, lecture_cues, target_sec, video_dur)
        if scenes and subs and llm_client.provider_for(api_key):
            try:
                enrich_topics_with_ai(scenes, subs, api_key, job.setdefault("topic_cache", {}))
            except Exception as e:
                job["warnings"].append(f"AI: topic notes could not be written ({e}).")
    else:
        if use_ai and api_key and len(subs) > 10:
            try:
                style = PRESETS.get(preset, {}).get("name", "Story Cut")
                if spoiler_mode == "teaser_spoiler_free":
                    style += " [SPOILER-FREE TEASER: end at high tension before the climax]"
                if target_character:
                    style += f" [POV focus: {target_character}]"
                ai = generate_ai_narrative_cut(format_script_for_llm(subs), int(target_minutes), video_dur, api_key,
                                               style_preference=style)
                scenes = refine_scene_boundaries(ai["scenes"], shot_cuts, silences, video_dur) or None
                job["narrative_overview"] = ai.get("overview", "")
            except Exception as e:
                job["warnings"].append(f"AI: AI Director failed ({e}). Used the offline story engine.")
        ai_scenes = None
        if scenes and sum(x["end"] - x["start"] for x in scenes) < target_sec * 0.9:
            ai_scenes, scenes = scenes, None   # the AI Director picked too little: fill up from the story engine
        if not scenes:
            essence = job.get("essence")
            if essence is None or essence.get("is_generic"):
                essence = story_blueprint(job, subs, video_dur, api_key)
                job["essence"] = essence
            if essence.get("is_generic"):
                job["warnings"].append("AI: no AI model could find this film's story beats right now, so a generic "
                                       "three-act template was used. Click Recalculate to try again.")
            candidates = build_candidates(subs, energy_index, shot_cuts, video_dur, target_character)
            chosen = normalize_filters(filters, preset)
            focus = focus_names_for(essence, chosen, target_character)
            per_filter = {}
            for f in chosen:
                found = detect_flavor_segments(f, subs, job.get("energy"), shot_cuts, silences, video_dur) if f in DETECTOR_FILTERS else []
                found += ml_detectors.audio_segments(job.get("audio_tags"), f)
                found += visual_scenes.visual_segments(job.get("visuals"), f)
                if found:
                    per_filter[f] = dedupe_segments(found)
            segments = round_robin(per_filter)
            job["warnings"] = [w for w in job["warnings"] if not w.startswith(("Flavor:", "Filters:"))]
            if chosen:
                found = []
                for f in chosen:
                    tagged = sum(1 for m in essence.get("milestones", []) if f in beat_tags(m))
                    detected = len(per_filter.get(f, []))
                    bits = ([f"{detected} found in the film"] if detected else []) + \
                           ([f"{tagged} story beats"] if tagged else [])
                    found.append(f"{BEAT_FILTER_LABELS[f]}: {', '.join(bits) if bits else 'none found'}")
                job["warnings"].append(f"Filters: {'; '.join(found)}. Up to {int(round(filter_share * 100))}% of the "
                                       f"time goes to filter scenes; matching story beats are kept first.")
            if target_character and not focus:
                job["warnings"].append(f"Filters: '{target_character}' was not found in the story beats.")
            cues = [(float(x["start"]), float(x["end"])) for x in subs if ANY_TAG.sub(" ", x.get("text", "")).strip()]
            scenes = compute_local_heuristic_summary(video_dur, target_sec, candidates, shot_cuts, silences, preset,
                                                     spoiler_mode, target_character, essence["milestones"],
                                                     flavor_segments=segments, flavor_share=filter_share,
                                                     filters=chosen, focus_names=focus, dialogue_cues=cues)
            job["filters"], job["filter_share"] = chosen, filter_share
            job["beat_characters"] = beat_character_names(essence)
        if ai_scenes:
            picked = sum(x["end"] - x["start"] for x in ai_scenes)
            scenes = fill_to_target(ai_scenes, scenes, target_sec)
            job["warnings"].append(f"AI: the AI Director picked {format_seconds(picked)}, so the most important story "
                                   f"scenes were added to reach your length.")

    if enforce_fair_use:
        scenes = enforce_fair_use_clipping(scenes, max_clip_sec=max_clip_sec)
    for s in scenes:
        s.setdefault("selected", True)
    return generate_scene_thumbnails(job["video_path"], scenes, job["job_id"])


PUBLIC_FIELDS = ("job_id", "status", "progress", "message", "metadata", "target_minutes", "preset", "spoiler_mode",
                 "target_character", "characters", "enforce_fair_use", "max_clip_sec", "total_selected_sec",
                 "total_selected_formatted", "narrative_overview", "scenes", "viral_shorts", "voiceover_ready",
                 "output_file", "error", "content_mode", "film_title", "warnings", "subtitle_source", "language", "voice",
                 "narration_stats", "filters", "filter_share", "beat_characters", "quality", "visuals", "rights", "private")


def job_public(job: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: job.get(k) for k in PUBLIC_FIELDS}
    ess = job.get("essence") or {}
    out.update({"essence_theme": ess.get("essence_theme", ""), "essence_source": ess.get("source"),
                "essence_milestones": ess.get("milestones", []), "timing_note": ess.get("timing_note"),
                "essence_is_generic": ess.get("is_generic", False), "has_render": bool(job.get("render_info")),
                "subtitle_count": len(job.get("subtitles") or []),
                "youtube_id": job.get("youtube_id") or youtube_id_from_path(job.get("video_path", "")),
                "private_expires_in": privacy.expires_in(job["job_id"]) if job.get("private") else None})
    return out


def _tail(text: str, n: int = 3) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return " | ".join(lines[-n:])[-500:]


# =============================================================================
# MODELS
# =============================================================================
class ProbeRequest(BaseModel):
    video_path: Optional[str] = None
    youtube_url: Optional[str] = None


class DownloadRequest(BaseModel):
    url: str
    max_height: int = Field(720, ge=240, le=2160)
    rights_basis: Optional[str] = None      # own | permission | private (None: go by the licence check)
    force_private: bool = False


class AnalyzeRequest(BaseModel):
    video_path: str
    rights_basis: Optional[str] = None      # own | permission | private
    force_private: bool = False
    target_minutes: int = Field(DEFAULT_TARGET_MINUTES, ge=1, le=240)
    preset: str = "story_focused"
    spoiler_mode: str = "full_cut"
    target_character: Optional[str] = None
    gemini_api_key: Optional[str] = None
    external_srt_path: Optional[str] = None
    use_ai_director: bool = False
    enforce_fair_use: bool = False
    max_clip_sec: float = Field(7.0, ge=2.0, le=120.0)
    content_mode: str = "movie"
    film_title: Optional[str] = None
    use_vision: bool = True
    filters: List[str] = Field(default_factory=list)
    filter_share: float = Field(0.4, ge=0.1, le=0.7)
    auto_transcribe: bool = True


class RecalculateRequest(BaseModel):
    target_minutes: int = Field(..., ge=1, le=240)
    preset: str = "story_focused"
    spoiler_mode: str = "full_cut"
    target_character: Optional[str] = None
    enforce_fair_use: bool = False
    max_clip_sec: float = Field(7.0, ge=2.0, le=120.0)
    content_mode: Optional[str] = None
    use_ai_director: bool = False
    filters: List[str] = Field(default_factory=list)
    filter_share: float = Field(0.4, ge=0.1, le=0.7)
    gemini_api_key: Optional[str] = None


class ToggleSceneRequest(BaseModel):
    scene_index: int
    selected: bool


class NarrateRequest(BaseModel):
    voice: Optional[str] = None
    style: str = "gap"   # gap | story | scene_intro
    use_ai: bool = True
    language: str = "English"
    gemini_api_key: Optional[str] = None
    azure_key: Optional[str] = None
    azure_region: Optional[str] = None


class RenderRequest(BaseModel):
    output_filename: Optional[str] = None
    render_mode: str = "cinematic_nvenc"
    include_voiceover: bool = False
    normalize_audio: bool = True
    horizontal_flip: bool = False
    smooth_audio: bool = True
    voice: Optional[str] = None
    language: Optional[str] = None
    azure_key: Optional[str] = None
    azure_region: Optional[str] = None


class TrailerRequest(BaseModel):
    length_sec: int = Field(90, ge=30, le=180)
    spoiler_free: bool = True
    vertical: bool = False


class ShortsRequest(BaseModel):
    pan_and_scan: bool = False
    burn_captions: bool = False
    caption_style: str = "karaoke"     # karaoke | pop | plain | classic
    count: int = Field(5, ge=1, le=10)
    max_duration_sec: float = Field(45.0, ge=10.0, le=90.0)


class WebhookDispatchRequest(BaseModel):
    webhook_url: str


class SeasonScanRequest(BaseModel):
    folder_path: str


class SeasonProcessRequest(BaseModel):
    folder_path: str
    target_minutes: float = Field(45.0, ge=5.0, le=300.0)
    preset: str = "story_focused"
    render_mode: str = "cinematic_nvenc"


class WatchfolderConfigRequest(BaseModel):
    watch_dir: str
    output_dir: Optional[str] = None
    target_minutes: int = Field(15, ge=3, le=120)
    render_mode: str = "stream_copy"
    check_interval_sec: int = Field(5, ge=2, le=3600)


class TranscribeRequest(BaseModel):
    video_path: str
    model_size: str = "base"
    engine: str = "auto"          # auto | local | groq
    language: Optional[str] = None


class WatchGuideRequest(BaseModel):
    title: str
    runtime_min: float = Field(..., gt=10, le=600)
    target_minutes: float = Field(15, ge=3, le=120)
    spoiler_mode: str = "full_cut"
    language: str = "English"
    offset_sec: float = Field(0.0, ge=-900, le=900)
    gemini_api_key: Optional[str] = None
    voice: Optional[str] = None


class CleanupRequest(BaseModel):
    older_than_days: float = Field(7.0, ge=0.0, le=3650.0)
    include_downloads: bool = False
    include_uploads: bool = False


# =============================================================================
# HEALTH / INGEST
# =============================================================================
@app.get("/api/health")
def health():
    return {"status": "ok", "version": APP_VERSION, "nvenc": check_nvenc_support(), "gpu_name": get_gpu_name(),
            "voices": {k: dict(v, ready=voice_ready(k)) for k, v in AVAILABLE_VOICES.items()},
            "default_voices": {lang: default_voice_for_language(lang) for lang in ("Hindi", "English")},
            "tts_status": {"gemini": tts_engines.gemini_available(), "sarvam": tts_engines.sarvam_available(),
                           "indic_parler": tts_engines.parler_status()},
            "ml_detectors": ml_detectors.available(), "whisper_available": is_whisper_available(),
            "gemini_fallback_model": DEFAULT_GEMINI_MODEL, "lan_mode": LAN_MODE, "port": SERVER_PORT,
            "app_dir": str(BASE_DIR), "local_ai_model": llm_client.ollama_model(),
            "ai_writer": llm_client.describe(effective_key()), "gemini_env_key": bool(effective_key()),
            "ai_providers": llm_usage.summary(), "groq_transcription": groq_transcription_available(),
            "azure_speech_configured": bool(os.environ.get("CINECUT_AZURE_SPEECH_KEY") and os.environ.get("CINECUT_AZURE_SPEECH_REGION")),
            "terms": {"version": TERMS_VERSION, "accepted": terms_accepted()}, "country": rights.COUNTRY,
            "private_ttl_min": privacy.TTL_SEC // 60}


def _download_with_ytdlp(url: str, task: Dict[str, Any], max_height: int = 720) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a full http(s) URL.")
    dl_dir = TEMP_DIR / "downloads" / uuid.uuid4().hex[:10]
    dl_dir.mkdir(parents=True, exist_ok=True)
    fmt = (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}][ext=mp4]/"
           f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b")
    cmd = ["yt-dlp", "--no-playlist", "--newline", "--progress", "--no-simulate", "-i",
           "-f", fmt, "--merge-output-format", "mp4",
           "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,hi.*", "--convert-subs", "srt",
           "--print", "before_dl:TITLE:%(title)s", "--print", "after_move:FILE:%(filepath)s",
           "-o", str(dl_dir / "%(title).80s [%(id)s].%(ext)s"), url]
    title, file_path, err_lines = "", None, []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("TITLE:"):
            title = line[6:].strip()
        elif line.startswith("FILE:"):
            file_path = line[5:].strip()
        m = re.search(r"\[download\]\s+([\d\.]+)%", line)
        if m:
            task["progress"] = min(99.0, float(m.group(1)))
            task["message"] = f"Downloading... {m.group(1)}%"
        if "ERROR" in line or "WARNING" in line:
            err_lines.append(line)
    proc.wait()

    if not file_path or not os.path.exists(file_path):
        vids = [p for p in dl_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS]
        file_path = str(max(vids, key=lambda p: p.stat().st_size)) if vids else None
    if not file_path:
        hint = ("yt-dlp needs a JavaScript runtime for YouTube: install Deno with 'winget install DenoLand.Deno' "
                "and update yt-dlp ('python -m pip install -U yt-dlp').")
        raise RuntimeError(f"Download failed: {' | '.join(err_lines[-2:])[-300:] or 'no video file produced'}. {hint}")
    subs = rank_subtitle_files(list(dl_dir.glob("*.srt")))
    meta = get_video_metadata(file_path)
    meta["youtube_title"] = title or Path(file_path).stem
    meta["subtitle_path"] = str(subs[0]) if subs else None
    return meta


def _checked_link(url: str, basis: Optional[str], force_private: bool):
    """Licence check before anything is downloaded; DRM services are refused."""
    try:
        report = rights.check_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if report["status"] == "blocked":
        raise HTTPException(status_code=403, detail=" ".join(report["reasons"]))
    if basis in ("own", "permission"):
        rights.record_declaration(url, basis, report, TERMS_VERSION)
    return report, rights.decide(report, basis, force_private)


def _register_download(meta: Dict[str, Any], report: Dict[str, Any], decision: Dict[str, Any], hold_id: str) -> None:
    """Remembers the licence of a downloaded file. A private download is deleted after the private-viewing time
    even if it is never analyzed."""
    entry = {"report": report, "decision": decision, "dl_dir": str(Path(meta["file_path"]).parent), "downloaded": True, "hold_id": None}
    if decision["status"] == "private":
        entry["hold_id"] = hold_id
        privacy.register(hold_id, [entry["dl_dir"]])
    rights.RIGHTS_BY_PATH[_path_key(meta["file_path"])] = entry
    meta["rights"] = decision


@app.post("/api/probe")
def probe_video(req: ProbeRequest):
    if req.youtube_url and not req.video_path:
        require_terms()
        report, decision = _checked_link(req.youtube_url, None, False)
        try:
            meta = _download_with_ytdlp(req.youtube_url, {})
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        _register_download(meta, report, decision, f"dl_{uuid.uuid4().hex[:8]}")
        return {"success": True, "metadata": meta}
    path = clean_user_path(req.video_path)
    if not path:
        raise HTTPException(status_code=400, detail="Video path or URL must be provided.")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        meta = get_video_metadata(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    meta["subtitle_path"] = find_sidecar_subtitles(path)
    return {"success": True, "metadata": meta}


@app.post("/api/download")
def start_download(req: DownloadRequest):
    """Checks the link's licence, then downloads it with yt-dlp in the background (poll /api/task/{task_id})."""
    require_terms()
    report, decision = _checked_link(req.url, req.rights_basis, req.force_private)
    task_id = f"dl_{uuid.uuid4().hex[:8]}"
    task = {"task_id": task_id, "kind": "download", "status": "running", "progress": 0.0,
            "message": "Starting download...", "result": None, "error": None, "rights": decision}
    TASKS[task_id] = task

    def run():
        try:
            meta = _download_with_ytdlp(req.url, task, req.max_height)
            _register_download(meta, report, decision, task_id)
            task.update({"status": "done", "progress": 100.0, "message": "Download complete.", "result": {"metadata": meta}})
        except Exception as e:
            task.update({"status": "error", "error": str(e), "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/task/{task_id}")
def get_task(task_id: str):
    if not ID_RE.match(task_id or "") or task_id not in TASKS:
        if task_id in privacy.DELETED:
            raise HTTPException(status_code=410, detail="This private summary has been deleted, as promised.")
        raise HTTPException(status_code=404, detail="Task not found.")
    task = TASKS[task_id]
    _touch_private(task_id, task)
    return {k: v for k, v in task.items() if not k.startswith("_")}


@app.post("/api/upload")
def upload_video(file: UploadFile = File(...)):
    """Browser upload (for small files; for big movies use the local path, which needs no copy)."""
    require_terms()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(file.filename or "upload.mp4").name).strip(" .") or "upload.mp4"
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        raise HTTPException(status_code=400, detail="Unsupported file type.")
    uploads_dir = TEMP_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / f"{uuid.uuid4().hex[:6]}_{name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f, length=16 * 1024 * 1024)
    try:
        meta = get_video_metadata(str(dest))
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    rights.RIGHTS_BY_PATH[_path_key(str(dest))] = {"report": None, "dl_dir": str(dest), "uploaded": True}
    meta["rights"] = rights.decide(None, None)
    return {"success": True, "video_path": str(dest), "metadata": meta}


# =============================================================================
# ANALYSIS & EDITING
# =============================================================================
@app.post("/api/analyze")
def analyze_video(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    require_terms()
    clean_path = clean_user_path(req.video_path)
    if not os.path.isfile(clean_path):
        raise HTTPException(status_code=404, detail="Video file not found.")
    entry = rights.RIGHTS_BY_PATH.get(_path_key(clean_path)) or {}
    # a download that was private stays private unless the user now declares their rights
    stay_private = (entry.get("decision") or {}).get("status") == "private" and req.rights_basis not in ("own", "permission")
    decision = rights.decide(entry.get("report"), req.rights_basis, req.force_private or stay_private)
    if decision.get("status") == "blocked":
        raise HTTPException(status_code=403, detail=" ".join(decision.get("reasons") or ["Blocked."]))
    if req.rights_basis in ("own", "permission") and not entry.get("downloaded"):
        rights.record_declaration(clean_path, req.rights_basis, entry.get("report"), TERMS_VERSION)
    private = decision["status"] == "private"
    job_id = uuid.uuid4().hex[:8]
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    if private:
        (job_dir / "private").mkdir(exist_ok=True)
        owned = [entry["dl_dir"]] if entry.get("dl_dir") and (entry.get("downloaded") or entry.get("uploaded")) else []
        for other_id, other in list(JOBS.items()):       # the newest private job owns a downloaded source
            if other.get("private") and other.get("video_path") == clean_path:
                privacy.drop_paths(other_id, owned)
        privacy.register(job_id, [str(job_dir)] + owned)
        privacy.forget(entry.get("hold_id"))
    mode = req.content_mode if req.content_mode in ("movie", "lecture") else "movie"
    job: Dict[str, Any] = {
        "job_id": job_id, "video_path": clean_path, "status": "analyzing", "progress": 5,
        "message": "Reading the video...", "content_mode": mode, "target_minutes": req.target_minutes,
        "preset": req.preset, "spoiler_mode": req.spoiler_mode, "target_character": req.target_character,
        "gemini_api_key": effective_key(req.gemini_api_key), "film_title": display_title((req.film_title or "").strip() or Path(clean_path).stem),
        "metadata": None, "characters": [], "scenes": [], "viral_shorts": [], "voiceover_ready": False,
        "output_file": None, "error": None, "warnings": [], "enforce_fair_use": req.enforce_fair_use,
        "max_clip_sec": req.max_clip_sec, "rights": decision, "private": private,
        "private_dir": str(job_dir / "private") if private else None
    }
    JOBS[job_id] = job

    def step(pct: float, msg: str):
        job["progress"], job["message"] = pct, msg

    def run():
        with privacy.private_scope(private):
            _analyze_body()

    def _analyze_body():
        try:
            meta = get_video_metadata(clean_path)
            job["metadata"] = meta
            dur = meta["duration_sec"]
            if dur <= 0:
                raise ValueError("Could not read the video duration.")

            step(12, "Reading subtitles / transcript...")
            subs, source = load_subtitles(clean_path, meta, req.external_srt_path, job_dir)
            job["subtitles"], job["subtitle_source"] = subs, source
            job["characters"] = extract_character_roster(subs) if subs else []
            if not subs and req.auto_transcribe and groq_transcription_available():
                cache_srt = transcript_cache_path(clean_path)
                if private and not cache_srt.exists():
                    cache_srt = job_dir / "transcript.srt"      # private viewing: deleted with the job
                if cache_srt.exists():
                    subs = parse_srt_file(str(cache_srt))
                if not subs:
                    step(13, "No subtitles found: transcribing the dialogue with Groq Whisper (free tier)...")
                    cache_srt.parent.mkdir(parents=True, exist_ok=True)
                    ok, subs, tmsg = transcribe_with_groq(clean_path, str(cache_srt), None,
                                                          progress=lambda p, m: step(13 + p * 0.1, m))
                    if not ok:
                        job["warnings"].append(f"Automatic transcription did not finish: {tmsg}")
                        subs = []
                if subs:
                    source = "Groq Whisper transcript (automatic)"
                    job["subtitles"], job["subtitle_source"] = subs, source
                    job["words"] = load_words(cache_srt)
                    job["characters"] = extract_character_roster(subs)
            if not subs:
                job["warnings"].append(
                    "No transcript found, so the lecture cut uses slide changes only. Use a YouTube URL with captions, "
                    "add an .srt file, or run Whisper." if mode == "lecture" else
                    "No subtitles found. Scenes are chosen from story position, loudness and editing pace.")

            step(25, "Analyzing the full audio track and every shot change (in parallel)...")
            audio, cuts, from_cache = cached_analysis(clean_path, 0.25 if mode == "lecture" else 0.35, dur)
            if from_cache:
                step(60, "Reused the saved analysis of this video.")
            job["silences"], job["energy"], job["shot_cuts"] = audio["silences"], audio["energy"], cuts
            job["audio_tags"] = audio.get("audio_tags")
            vision_key = effective_key(req.gemini_api_key)
            if mode == "movie" and req.use_vision and visual_scenes.gc.all_keys(vision_key):
                step(61, "The AI is looking at scenes with little dialogue...")
                try:
                    job["visuals"] = visual_scenes.describe_scenes(
                        clean_path, job["film_title"], job.get("subtitles") or [], job["energy"], job["shot_cuts"], dur,
                        vision_key, progress=lambda p, m: step(61 + p * 0.03, m))
                except Exception as e:
                    job["visuals"] = []
                    job["warnings"].append(f"AI: could not look at scenes this time ({e}).")

            step(65, "Curating scenes..." if mode == "movie" else "Finding the key explanations...")
            scenes = curate_scenes(job, req.target_minutes, req.preset, req.spoiler_mode, req.target_character,
                                   req.enforce_fair_use, req.max_clip_sec, req.use_ai_director, effective_key(req.gemini_api_key),
                                   req.filters, req.filter_share)
            job["scenes"] = scenes
            update_totals(job)
            job["status"] = "ready"
            step(100, f"Analysis complete. Curated {len(scenes)} scenes ({job['total_selected_formatted']}).")
        except Exception as e:
            job.update({"status": "error", "error": str(e), "message": f"Error: {e}"})

    background_tasks.add_task(run)
    return {"job_id": job_id, "status": "analyzing"}


@app.get("/api/job/{job_id}")
def get_job_status(job_id: str):
    return job_public(get_job(job_id))


@app.post("/api/recalculate/{job_id}")
def recalculate_job(job_id: str, req: RecalculateRequest):
    job = get_job(job_id)
    if job["status"] in ("analyzing", "rendering") or not job.get("metadata") or "shot_cuts" not in job:
        raise HTTPException(status_code=400, detail="Job is not ready yet.")
    if req.content_mode in ("movie", "lecture"):
        job["content_mode"] = req.content_mode
    api_key = effective_key(req.gemini_api_key, job.get("gemini_api_key"))
    with privacy.private_scope(job.get("private")):
        scenes = curate_scenes(job, req.target_minutes, req.preset, req.spoiler_mode, req.target_character,
                               req.enforce_fair_use, req.max_clip_sec, req.use_ai_director, api_key,
                               req.filters, req.filter_share)
    job.update({"scenes": scenes, "target_minutes": req.target_minutes, "preset": req.preset,
                "spoiler_mode": req.spoiler_mode, "target_character": req.target_character,
                "enforce_fair_use": req.enforce_fair_use, "max_clip_sec": req.max_clip_sec,
                "voiceover_ready": False, "viral_shorts": [], "status": "ready", "error": None})
    update_totals(job)
    return {"success": True, "scenes": scenes, "total_selected_formatted": job["total_selected_formatted"],
            "warnings": job.get("warnings", [])}


@app.post("/api/toggle_scene/{job_id}")
def toggle_scene(job_id: str, req: ToggleSceneRequest):
    job = get_job(job_id)
    if not 0 <= req.scene_index < len(job.get("scenes", [])):
        raise HTTPException(status_code=400, detail="Invalid scene index.")
    job["scenes"][req.scene_index]["selected"] = req.selected
    update_totals(job)
    return {"success": True, "total_selected_formatted": job["total_selected_formatted"]}


@app.post("/api/narrate/{job_id}")
async def generate_voiceovers(job_id: str, req: NarrateRequest):
    job = get_job(job_id)
    if not job.get("scenes"):
        raise HTTPException(status_code=400, detail="Analyze the video first.")
    language = req.language if req.language in LANGUAGE_DEFAULT_VOICE else "English"
    voice = req.voice if req.voice in AVAILABLE_VOICES else default_voice_for_language(language)
    api_key = effective_key(req.gemini_api_key, job.get("gemini_api_key"))
    scenes = job["scenes"]
    if req.style == "scene_intro":
        scenes = await asyncio.to_thread(generate_ai_bridge_narrations, scenes, film_title(job), api_key,
                                         language, job.get("content_mode", "movie"))
        stats: Dict[str, Any] = {"style": "scene_intro"}
    else:
        stats = await asyncio.to_thread(generate_gap_narrations, scenes, job.get("subtitles") or [],
                                        film_title(job), language, api_key if req.use_ai else None,
                                        story_milestones(job),
                                        job.get("content_mode", "movie"), None, req.use_ai,
                                        job["metadata"]["duration_sec"], req.style == "story",
                                        beat_character_names(job.get("essence") or {}), visuals=job.get("visuals"))
        stats["style"] = req.style
    job["narration_stats"] = stats
    vo_dir = TEMP_DIR / job_id / "voiceover"
    vo_dir.mkdir(parents=True, exist_ok=True)
    engine = "azure" if ((req.azure_key and req.azure_region) or os.environ.get("CINECUT_AZURE_SPEECH_KEY")) else "edge"
    sem = asyncio.Semaphore(4)

    async def synth(i: int, s: Dict[str, Any]):
        text = s.get("bridge_narration")
        if not text:
            s.update({"voiceover_audio_path": None, "voiceover_url": None})
            return
        vk = voice_for_text(text, voice)
        path = tts_cache_path(vo_dir, text, vk, engine=engine)
        if not path.exists():
            async with sem:
                await synthesize_voiceover_async(text, vk, str(path), azure_key=req.azure_key, azure_region=req.azure_region)
        s.update({"voiceover_audio_path": str(path), "voiceover_url": f"/api/voiceover/{job_id}/{i}",
                  "voiceover_text": text, "voice": vk})

    batch_engine = AVAILABLE_VOICES.get(voice, {}).get("engine")
    if batch_engine in ("gemini", "parler"):
        todo = [(s.get("bridge_narration"), voice_for_text(s.get("bridge_narration"), voice)) for s in scenes if s.get("bridge_narration")]
        todo = [(t, vk) for t, vk in todo if AVAILABLE_VOICES.get(vk, {}).get("engine") == batch_engine
                and not tts_cache_path(vo_dir, t, vk, engine=engine).exists()]
        if todo:
            from backend.video_engine.narrator import synthesize_many
            await asyncio.to_thread(synthesize_many, [t for t, _ in todo], voice,
                                    [str(tts_cache_path(vo_dir, t, vk, engine=engine)) for t, vk in todo])
    try:
        await asyncio.gather(*(synth(i, s) for i, s in enumerate(scenes)))
        stats["revoiced_for_consistency"] = await asyncio.to_thread(ensure_one_voice, scenes, vo_dir, engine,
                                                                    req.azure_key, req.azure_region)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    stats["voice_engine"] = sorted({engine_of(s["voiceover_audio_path"]) for s in scenes if s.get("voiceover_audio_path")})
    job.update({"scenes": scenes, "voiceover_ready": True, "language": language, "voice": voice})
    return {"success": True, "scenes": scenes, "stats": stats}


@app.get("/api/voiceover/{job_id}/{scene_index}")
def serve_voiceover(job_id: str, scene_index: int):
    job = get_job(job_id)
    if not 0 <= scene_index < len(job.get("scenes", [])):
        raise HTTPException(status_code=404, detail="Scene not found.")
    p = job["scenes"][scene_index].get("voiceover_audio_path")
    base = (TEMP_DIR / job_id).resolve()
    if not p or base not in Path(p).resolve().parents or not os.path.exists(p):
        raise HTTPException(status_code=404, detail="Voiceover audio not found.")
    return FileResponse(p, media_type="audio/mpeg")


class VoiceTestRequest(BaseModel):
    voice: Optional[str] = None
    language: str = "English"
    azure_key: Optional[str] = None
    azure_region: Optional[str] = None


VOICE_SAMPLES = {
    "English": "This is how your CineCut narrator will sound.",
    "Hindi": "आपका सिनेकट नैरेटर कुछ इस तरह सुनाई देगा।",
    "Spanish": "Así sonará tu narrador de CineCut.",
    "French": "Voici la voix de votre narrateur CineCut.",
    "German": "So klingt dein CineCut-Erzähler.",
    "Japanese": "これがCineCutのナレーターの声です。",
}


@app.post("/api/voice_test")
async def voice_test(req: VoiceTestRequest):
    voice = req.voice if req.voice in AVAILABLE_VOICES else default_voice_for_language(req.language)
    lang = AVAILABLE_VOICES[voice]["language"]
    text = VOICE_SAMPLES.get(lang, VOICE_SAMPLES["English"])
    engine = "azure" if (req.azure_key and req.azure_region) else "edge"
    path = tts_cache_path(TEMP_DIR / "voice_tests", text, voice, engine=engine)
    if not path.exists():
        try:
            await synthesize_voiceover_async(text, voice, str(path), azure_key=req.azure_key, azure_region=req.azure_region)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    return FileResponse(str(path), media_type="audio/mpeg")


# =============================================================================
# SHORTS, EXPORTS
# =============================================================================
class QualityRequest(BaseModel):
    ai_check: bool = True
    fix: bool = False


@app.post("/api/quality/{job_id}")
def check_quality(job_id: str, req: QualityRequest):
    """Score of the current cut; optionally an AI viewer check, and a fix that adds what the viewer would miss."""
    job = get_job(job_id)
    if job["status"] in ("analyzing", "rendering") or not job.get("scenes"):
        raise HTTPException(status_code=400, detail="Analyze the video first.")
    result: Dict[str, Any] = {"before": quality_mod.score_cut(job).get("score")}
    api_key = effective_key(job.get("gemini_api_key"))
    if req.ai_check:
        try:
            chk = quality_mod.ai_check(job, api_key)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"The AI check could not run: {e}")
        if chk.get("error"):
            raise HTTPException(status_code=400, detail=chk["error"])
        job.setdefault("quality", {})["ai_check"] = chk
        if req.fix and chk.get("lost_points"):
            fix = quality_mod.fix_cut(job, chk)
            result["fix"] = fix
            if fix.get("added"):
                for sc in job["scenes"]:
                    sc.update({"bridge_narration": None, "narration_source": None, "narration_covers": None,
                               "voiceover_audio_path": None, "voiceover_url": None})
                job["voiceover_ready"] = False
                job["scenes"] = generate_scene_thumbnails(job["video_path"], job["scenes"], job_id)
                try:
                    job["quality"]["ai_check"] = quality_mod.ai_check(job, api_key)
                except Exception:
                    pass
    update_totals(job)
    result.update({"quality": job["quality"], "total_selected_formatted": job["total_selected_formatted"]})
    return result


@app.post("/api/trailer/{job_id}")
def make_trailer_endpoint(job_id: str, req: TrailerRequest):
    job = get_job(job_id)
    if job["status"] in ("analyzing", "rendering") or not job.get("metadata"):
        raise HTTPException(status_code=400, detail="Analyze the video first.")
    if job.get("content_mode") == "lecture":
        raise HTTPException(status_code=400, detail="Trailers are made for films. For lectures, use the study cut and shorts.")
    try:
        info = make_trailer(job, req.length_sec, req.spoiler_free, req.vertical)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"The trailer could not be made: {e}")
    append_end_card(info["file"], credit_for(job.get("rights"), film_title(job)), film_title(job), job.get("language") or "English", 4.0)
    job["trailer_file"] = info["file"]
    info["video_url"] = f"/api/trailer/{job_id}/video"
    return info


@app.get("/api/trailer/{job_id}/video")
def stream_trailer(job_id: str, download: bool = False):
    job = get_job(job_id)
    f = job.get("trailer_file")
    if not f or not os.path.exists(f):
        raise HTTPException(status_code=404, detail="Make the trailer first.")
    if download:
        no_export(job)
    headers = {"Content-Disposition": f'attachment; filename="{Path(f).name}"'} if download else None
    return FileResponse(f, media_type="video/mp4", headers=headers)


@app.post("/api/generate_shorts/{job_id}")
def generate_shorts(job_id: str, req: Optional[ShortsRequest] = None):
    job = get_job(job_id)
    if not job.get("scenes"):
        raise HTTPException(status_code=400, detail="Analyze the video first.")
    req = req or ShortsRequest()
    shorts = extract_viral_shorts(job["video_path"], job["scenes"], job_id, count=req.count,
                                  max_duration_sec=req.max_duration_sec, pan_and_scan=req.pan_and_scan,
                                  subtitles=job.get("subtitles"), burn_captions=req.burn_captions,
                                  energy=job.get("energy"), shot_cuts=job.get("shot_cuts"),
                                  video_duration=job["metadata"]["duration_sec"],
                                  content_mode=job.get("content_mode", "movie"),
                                  caption_style=req.caption_style, words=job.get("words"))
    for s in shorts:
        append_end_card(s["file_path"], credit_for(job.get("rights"), film_title(job)), film_title(job), job.get("language") or "English", 3.0)
    job["viral_shorts"] = shorts
    if not shorts:
        raise HTTPException(status_code=500, detail="No shorts could be generated from the selected scenes.")
    return {"success": True, "viral_shorts": shorts}


@app.get("/api/shorts/{job_id}/{short_index}")
def stream_short(job_id: str, short_index: int, download: bool = False):
    if not ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Not found.")
    if download and job_id in JOBS:
        no_export(JOBS[job_id])
    file_path = safe_child(TEMP_DIR, job_id, "viral_shorts", f"short_{short_index:02d}.mp4")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Short video not found.")
    headers = {"Content-Disposition": f'attachment; filename="Short_{short_index:02d}.mp4"'} if download else None
    return FileResponse(str(file_path), media_type="video/mp4", headers=headers)


@app.get("/api/export/shorts_pack/{job_id}")
def export_shorts_zip(job_id: str):
    no_export(get_job(job_id))
    zip_path = bundle_shorts_into_zip(job_id)
    if not zip_path:
        raise HTTPException(status_code=404, detail="Generate shorts first.")
    return FileResponse(zip_path, media_type="application/zip", filename=f"CineCut_Shorts_{job_id}.zip")


@app.get("/api/export/edl/{job_id}")
def export_edl(job_id: str):
    job = get_job(job_id)
    no_export(job)
    edl_file = TEMP_DIR / job_id / "timeline.edl"
    export_cmx3600_edl(job["scenes"], job["video_path"], str(edl_file), fps=(job.get("metadata") or {}).get("fps", 24.0))
    return FileResponse(str(edl_file), media_type="text/plain", filename="CineCut_DaVinci_Resolve.edl")


@app.get("/api/export/xml/{job_id}")
def export_xml(job_id: str):
    job = get_job(job_id)
    no_export(job)
    meta = job.get("metadata") or {}
    xml_file = TEMP_DIR / job_id / "sequence.xml"
    export_premiere_fcpxml(job["scenes"], job["video_path"], str(xml_file), video_width=meta.get("width", 1920),
                           video_height=meta.get("height", 1080), fps=meta.get("fps", 24.0))
    return FileResponse(str(xml_file), media_type="application/xml", filename="CineCut_Premiere_Sequence.xml")


@app.get("/api/export/youtube_cut/{job_id}")
def export_youtube_cut(job_id: str, download: bool = False):
    """Condensed cut that plays the ORIGINAL YouTube upload via the official embedded player (copies nothing)."""
    job = get_job(job_id)
    if download:
        no_export(job)
    video_id = job.get("youtube_id") or youtube_id_from_path(job["video_path"])
    if not video_id:
        raise HTTPException(status_code=400, detail="This video did not come from YouTube, so there is no original to embed.")
    html = build_youtube_cut_html(video_id, film_title(job), job["scenes"], job.get("language") or "English")
    headers = {"Content-Disposition": 'attachment; filename="CineCut_YouTube_Cut.html"'} if download else None
    return HTMLResponse(html, headers=headers)


@app.get("/api/export/youtube_links/{job_id}")
def export_youtube_links(job_id: str):
    job = get_job(job_id)
    no_export(job)
    video_id = job.get("youtube_id") or youtube_id_from_path(job["video_path"])
    if not video_id:
        raise HTTPException(status_code=400, detail="This video did not come from YouTube.")
    return PlainTextResponse("\n".join(timestamp_links(video_id, job["scenes"])))


def _render_durations(job: Dict[str, Any]) -> Optional[List[float]]:
    info = job.get("render_info") or {}
    if info.get("signature") == selected_signature(job.get("scenes", [])):
        return info.get("durations")
    return None


@app.get("/api/export/srt/{job_id}")
def export_synced_srt(job_id: str):
    job = get_job(job_id)
    no_export(job)
    srt_file = TEMP_DIR / job_id / "CineCut_Summary_Synced.srt"
    generate_synced_srt(job.get("subtitles") or [], job["scenes"], str(srt_file), durations=_render_durations(job))
    return FileResponse(str(srt_file), media_type="text/plain", filename="CineCut_Summary_Synced.srt")


@app.get("/api/export/notes/{job_id}")
def export_study_notes(job_id: str):
    job = get_job(job_id)
    no_export(job)
    notes = generate_study_notes(job["scenes"], job.get("subtitles") or [], film_title(job), _render_durations(job))
    path = TEMP_DIR / job_id / "CineCut_Study_Notes.md"
    path.write_text(notes, encoding="utf-8")
    return FileResponse(str(path), media_type="text/markdown", filename="CineCut_Study_Notes.md")


@app.get("/api/youtube/pack/{job_id}")
def youtube_pack(job_id: str):
    job = get_job(job_id)
    no_export(job)
    info = job.get("render_info") or {}
    chapters = info.get("chapters") if _render_durations(job) else None
    if not chapters:
        chapters, t = [], 0.0
        for s in sorted([s for s in job["scenes"] if s.get("selected", True)], key=lambda s: s["start"]):
            chapters.append({"title": s.get("title", "Scene"), "start": t, "end": t + s["duration"]})
            t += s["duration"]
    has_vo = any(s.get("voiceover_audio_path") for s in job["scenes"] if s.get("selected", True))
    pack = generate_youtube_upload_pack(film_title(job), chapters, job.get("language") or "English", ai_voice=has_vo,
                                        content_mode=job.get("content_mode", "movie"),
                                        total_duration=chapters[-1]["end"] if chapters else None)
    pack["rendered"] = bool(info)
    credit = (job.get("rights") or {}).get("attribution")
    pack["description"] = (pack.get("description") or "") + (f"\n\nSource: {credit}" if credit else "") + \
        "\nThis summary was made with AI (CineCut): the narration is AI-written and the voice is synthetic."
    return pack


# =============================================================================
# RENDER
# =============================================================================
@app.post("/api/render/{job_id}")
def render_job(job_id: str, req: RenderRequest, background_tasks: BackgroundTasks):
    job = get_job(job_id)
    if job["status"] in ("analyzing", "rendering"):
        raise HTTPException(status_code=409, detail="This job is busy.")
    if not job.get("scenes"):
        raise HTTPException(status_code=400, detail="Analyze the video first.")
    job.update({"status": "rendering", "progress": 0, "message": "Preparing render...", "error": None})

    def run():
        def on_progress(pct: float, msg: str):
            job["progress"], job["message"] = pct, msg
        try:
            language = req.language or job.get("language") or "English"
            preferred = req.voice if req.voice in AVAILABLE_VOICES else (job.get("voice") or default_voice_for_language(language))
            if req.include_voiceover:
                active = [s for s in job["scenes"] if s.get("selected", True)]
                if not any(s.get("bridge_narration") for s in active):
                    on_progress(1.0, "Writing narration for the parts that were cut out...")
                    job["narration_stats"] = generate_gap_narrations(
                        job["scenes"], job.get("subtitles") or [], film_title(job), language,
                        effective_key(job.get("gemini_api_key")), story_milestones(job),
                        job.get("content_mode", "movie"), None, True, job["metadata"]["duration_sec"],
                        names=beat_character_names(job.get("essence") or {}), visuals=job.get("visuals"))
                on_progress(2.0, "Synthesizing narration...")
                vo_dir = TEMP_DIR / job_id / "voiceover"
                vo_dir.mkdir(parents=True, exist_ok=True)
                engine = "azure" if ((req.azure_key and req.azure_region) or os.environ.get("CINECUT_AZURE_SPEECH_KEY")) else "edge"
                for i, s in enumerate(job["scenes"]):
                    text = s.get("bridge_narration") if s.get("selected", True) else None
                    if not text:
                        s.update({"voiceover_audio_path": None, "voiceover_url": None})
                        continue
                    vk = voice_for_text(text, preferred)
                    path = tts_cache_path(vo_dir, text, vk, engine=engine)
                    if not path.exists():
                        synthesize_voiceover(text, vk, str(path), azure_key=req.azure_key, azure_region=req.azure_region)
                    s.update({"voiceover_audio_path": str(path), "voiceover_text": text,
                              "voiceover_url": f"/api/voiceover/{job_id}/{i}", "voice": vk})
            info: Dict[str, Any] = {}
            output_file = render_summary_video(
                source_video_path=job["video_path"], scenes=job["scenes"], job_id=job_id,
                output_filename=req.output_filename, render_mode=req.render_mode, output_dir=job.get("private_dir"),
                include_voiceover=req.include_voiceover, normalize_audio=req.normalize_audio,
                horizontal_flip=req.horizontal_flip, smooth_audio=req.smooth_audio,
                progress_callback=on_progress, result_info=info,
                speech_cues=[(float(x["start"]), float(x["end"])) for x in (job.get("subtitles") or [])
                             if ANY_TAG.sub(" ", x.get("text", "")).strip()])
            on_progress(99.0, "Adding the credits card...")
            append_end_card(output_file, credit_for(job.get("rights"), film_title(job)), film_title(job), language)
            info["signature"] = selected_signature(job["scenes"])
            update_totals(job)
            job.update({"render_info": info, "output_file": output_file, "horizontal_flip": req.horizontal_flip,
                        "status": "rendered", "progress": 100,
                        "message": f"Rendered {Path(output_file).name} ({format_seconds(info.get('total_duration', 0))}) with chapter markers."})
        except Exception as e:
            job.update({"status": "error", "error": str(e), "message": f"Render error: {e}"})

    background_tasks.add_task(run)
    return {"job_id": job_id, "status": "rendering"}


@app.get("/api/video/final/{job_id}")
def stream_final_video(job_id: str, download: bool = False):
    job = get_job(job_id)
    out = job.get("output_file")
    if not out or not os.path.exists(out):
        raise HTTPException(status_code=404, detail="Rendered video not found.")
    if download:
        no_export(job)
    return FileResponse(out, media_type="video/mp4", filename=Path(out).name if download else None)


@app.post("/api/open_folder/{job_id}")
def open_output_folder(job_id: str):
    job = JOBS.get(job_id) if ID_RE.match(job_id or "") else None
    if job:
        no_export(job)
    target = Path(job["output_file"]).parent if job and job.get("output_file") else OUTPUT_DIR
    if sys.platform == "win32":
        os.startfile(str(target))
    return {"success": True, "path": str(target)}


# =============================================================================
# COPYRIGHT CHECK, LEGAL DRAFTS, SOCIAL
# =============================================================================
def _cut_facts(job: Dict[str, Any]) -> Dict[str, Any]:
    active = [s for s in job.get("scenes", []) if s.get("selected", True)]
    src = (job.get("metadata") or {}).get("duration_sec") or 0
    used = sum(s["duration"] for s in active)
    return {"active": active, "longest": max((s["duration"] for s in active), default=0.0),
            "share_pct": round(used / src * 100, 1) if src else None, "used": used}


@app.get("/api/fair_use/score/{job_id}")
def get_fair_use_score(job_id: str, horizontal_flip: bool = False):
    job = get_job(job_id)
    return calculate_fair_use_score(job.get("scenes", []), has_horizontal_flip=horizontal_flip,
                                    max_clip_sec=job.get("max_clip_sec", 7.0),
                                    source_duration_sec=(job.get("metadata") or {}).get("duration_sec"))


@app.get("/api/legal/dispute_pack/{job_id}")
def get_legal_dispute_pack(job_id: str):
    job = get_job(job_id)
    no_export(job)
    facts = _cut_facts(job)
    active = facts["active"]
    vo_pct = round(sum(1 for s in active if s.get("voiceover_audio_path") or s.get("bridge_narration")) / max(1, len(active)) * 100, 1)
    notice = generate_dmca_counter_notification(
        movie_title=film_title(job), total_scenes=len(active), vo_coverage_pct=vo_pct,
        max_clip_sec=job.get("max_clip_sec", 7.0), has_horizontal_flip=job.get("horizontal_flip", False),
        longest_clip_sec=facts["longest"], source_share_pct=facts["share_pct"],
        cut_duration_formatted=format_seconds(facts["used"]))
    legal_file = TEMP_DIR / job_id / f"Counter_Notice_DRAFT_{job_id}.txt"
    legal_file.write_text(notice, encoding="utf-8")
    return FileResponse(str(legal_file), media_type="text/plain", filename=legal_file.name)


@app.post("/api/legal/slate/{job_id}")
def generate_legal_slate(job_id: str):
    get_job(job_id)
    slate_file = TEMP_DIR / job_id / "disclosure_slate.mp4"
    if not generate_fair_use_slate_video(str(slate_file)) or not slate_file.exists():
        raise HTTPException(status_code=500, detail="Failed to render the disclosure slate.")
    return FileResponse(str(slate_file), media_type="video/mp4", filename=f"disclosure_slate_{job_id}.mp4")


@app.get("/api/social/pack/{job_id}")
def get_social_publishing_pack(job_id: str):
    job = get_job(job_id)
    if not job.get("viral_shorts"):
        raise HTTPException(status_code=400, detail="Generate shorts first.")
    no_export(job)
    return {"success": True, "pack": generate_social_publishing_pack(job["viral_shorts"], film_title(job))}


@app.post("/api/social/webhook/{job_id}")
def send_social_webhook(job_id: str, req: WebhookDispatchRequest):
    job = get_job(job_id)
    if not job.get("viral_shorts"):
        raise HTTPException(status_code=400, detail="Generate shorts first.")
    no_export(job)
    pack = generate_social_publishing_pack(job["viral_shorts"], film_title(job))
    payload = {"job_id": job_id, "movie_title": film_title(job), "shorts_count": len(pack), "shorts": pack}
    if not dispatch_publishing_webhook(req.webhook_url, payload):
        raise HTTPException(status_code=502, detail="Webhook dispatch failed. Check the URL (http/https) and connectivity.")
    return {"success": True, "message": "Publishing package sent to your webhook."}


# =============================================================================
# OFFLINE TRANSCRIPTION (WHISPER)
# =============================================================================
@app.get("/api/transcribe/status")
def transcribe_status():
    return {"whisper_available": is_whisper_available()}


@app.post("/api/transcribe_video")
def transcribe_video(req: TranscribeRequest):
    """Transcribes a video before analysis; the result becomes the subtitle file for Analyze."""
    path = clean_user_path(req.video_path)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Video file not found.")
    local_ok, groq_ok = is_whisper_available(), groq_transcription_available()
    engine = req.engine
    if engine == "auto":
        engine = "local" if local_ok else ("groq" if groq_ok else "none")
    if engine == "local" and not local_ok:
        raise HTTPException(status_code=400, detail="Local Whisper is not installed. Run: python -m pip install faster-whisper")
    if engine == "groq" and not groq_ok:
        raise HTTPException(status_code=400, detail="No GROQ_API_KEY in the .env file.")
    if engine not in ("local", "groq"):
        raise HTTPException(status_code=400, detail="No transcription engine: install faster-whisper, or add a GROQ_API_KEY to .env.")
    task_id = f"tr_{uuid.uuid4().hex[:8]}"
    srt_path = TEMP_DIR / "transcripts" / f"{task_id}.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    task = {"task_id": task_id, "kind": "transcribe", "status": "running", "progress": 5.0, "engine": engine,
            "message": "Starting transcription...", "result": None, "error": None}
    TASKS[task_id] = task

    def run():
        if engine == "groq":
            ok, subs, msg = transcribe_with_groq(path, str(srt_path), req.language,
                                                 progress=lambda p, m: task.update(progress=round(p, 1), message=m))
        else:
            task["message"] = "Transcribing locally with Whisper (this can take a while)..."
            ok, subs = transcribe_video_offline(path, str(srt_path), req.model_size)
            msg = f"Transcribed {len(subs)} lines." if ok else "No speech detected or transcription failed."
        if ok:
            task.update({"status": "done", "progress": 100.0, "message": msg,
                         "result": {"subtitle_path": str(srt_path), "lines": len(subs)}})
        else:
            task.update({"status": "error", "error": msg, "message": msg})

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/transcribe/{job_id}")
def transcribe_job_audio(job_id: str, background_tasks: BackgroundTasks):
    job = get_job(job_id)
    if not is_whisper_available():
        raise HTTPException(status_code=400, detail="Whisper is not installed. Run: python -m pip install faster-whisper")
    previous = job["status"]
    srt_path = TEMP_DIR / job_id / "offline_transcription.srt"

    def run():
        job.update({"status": "transcribing", "message": "Transcribing audio locally with Whisper..."})
        ok, subs = transcribe_video_offline(job["video_path"], str(srt_path))
        if ok:
            job["subtitles"] = subs
            job["characters"] = extract_character_roster(subs)
            job["message"] = f"Transcription complete: {len(subs)} lines. Click Recalculate to use it."
        else:
            job["message"] = "Transcription found no speech."
        job["status"] = previous if previous in ("ready", "rendered") else "ready"

    background_tasks.add_task(run)
    return {"success": True, "message": "Whisper transcription started."}


# =============================================================================
# TV SEASON BATCH & WATCHFOLDER
# =============================================================================
@app.post("/api/season/scan")
def scan_season(req: SeasonScanRequest):
    try:
        return {"success": True, **scan_season_folder(req.folder_path)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/season/process")
def process_season(req: SeasonProcessRequest, background_tasks: BackgroundTasks):
    require_terms()
    batch_id = f"season_{uuid.uuid4().hex[:8]}"
    batch = {"batch_id": batch_id, "status": "processing", "progress": 0, "message": "Scanning episodes...",
             "folder_path": req.folder_path, "target_minutes": req.target_minutes}
    SEASON_BATCH_JOBS[batch_id] = batch

    def run():
        try:
            def on_progress(pct: float, msg: str):
                batch["progress"], batch["message"] = pct, msg
            info = scan_season_folder(req.folder_path)
            res = assemble_season_recap(batch_id, req.folder_path, info["episodes"], req.target_minutes, req.preset,
                                        req.render_mode, on_progress)
            batch.update({"status": "completed", "progress": 100, "message": f"Season {res['season_num']} recap assembled.",
                          **{k: res[k] for k in ("output_file", "filename", "chapters", "total_duration_formatted", "warnings")}})
        except Exception as e:
            batch.update({"status": "error", "error": str(e), "message": f"Batch error: {e}"})

    background_tasks.add_task(run)
    return {"success": True, "batch_id": batch_id, "status": "processing"}


@app.get("/api/season/status/{batch_id}")
def get_season_status(batch_id: str):
    if batch_id not in SEASON_BATCH_JOBS:
        raise HTTPException(status_code=404, detail="Batch job not found.")
    return SEASON_BATCH_JOBS[batch_id]


@app.get("/api/season/download/{batch_id}")
def download_season_recap(batch_id: str):
    batch = SEASON_BATCH_JOBS.get(batch_id)
    if not batch or not batch.get("output_file") or not os.path.exists(batch["output_file"]):
        raise HTTPException(status_code=404, detail="Rendered season recap not found.")
    return FileResponse(batch["output_file"], media_type="video/mp4", filename=batch.get("filename", "Season_Recap.mp4"))


@app.post("/api/watchfolder/start")
def start_watchfolder(req: WatchfolderConfigRequest):
    require_terms()
    daemon = get_watchfolder_daemon()
    if daemon and daemon.is_running():
        daemon.stop()
    daemon = WatchfolderDaemon(req.watch_dir, req.output_dir, req.target_minutes, req.render_mode, req.check_interval_sec)
    try:
        daemon.start()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    set_watchfolder_daemon(daemon)
    return {"success": True, "status": daemon.get_status()}


@app.post("/api/watchfolder/stop")
def stop_watchfolder():
    daemon = get_watchfolder_daemon()
    if daemon and daemon.is_running():
        daemon.stop()
        return {"success": True, "message": "Watchfolder stopped."}
    return {"success": True, "message": "Watchfolder was not running."}


@app.get("/api/watchfolder/status")
def watchfolder_status():
    daemon = get_watchfolder_daemon()
    if daemon:
        return daemon.get_status()
    return {"running": False, "watch_dir": "", "output_dir": "Same as Media Source", "target_minutes": 15,
            "render_mode": "stream_copy", "completed_count": 0, "completed_items": [], "recent_logs": []}


@app.post("/api/watchfolder/scan")
def trigger_watchfolder_scan():
    daemon = get_watchfolder_daemon()
    if not daemon or not daemon.is_running():
        raise HTTPException(status_code=400, detail="Watchfolder is not running.")
    return {"success": True, "processed": daemon.scan_once(), "status": daemon.get_status()}


# =============================================================================
# THUMBNAILS & PREVIEWS
# =============================================================================
@app.get("/api/thumb/{job_id}/{filename}")
def serve_thumbnail(job_id: str, filename: str):
    if not ID_RE.match(job_id or ""):
        raise HTTPException(status_code=404, detail="Not found.")
    file_path = safe_child(TEMP_DIR, job_id, "thumbnails", filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(str(file_path), media_type="image/jpeg")


@app.get("/api/preview/{job_id}/{scene_index}")
def preview_scene(job_id: str, scene_index: int):
    job = get_job(job_id)
    if not 0 <= scene_index < len(job.get("scenes", [])):
        raise HTTPException(status_code=400, detail="Invalid scene index.")
    scene = job["scenes"][scene_index]
    start, dur = scene["start"], min(scene["duration"], 20.0)
    preview_dir = TEMP_DIR / job_id / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_file = preview_dir / f"prev_{int(start * 100)}_{int(dur * 100)}.mp4"
    if not preview_file.exists():
        tmp = preview_dir / f"tmp_{uuid.uuid4().hex[:6]}.mp4"
        subprocess.run([FFMPEG_BIN, "-y", "-ss", f"{start:.2f}", "-i", job["video_path"], "-t", f"{dur:.2f}",
                        "-vf", "scale=640:-2,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", str(tmp)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if tmp.exists():
            os.replace(tmp, preview_file)
    if preview_file.exists():
        return FileResponse(str(preview_file), media_type="video/mp4")
    raise HTTPException(status_code=500, detail="Failed to generate preview.")


# =============================================================================
# STREAMING WATCH GUIDE (Netflix / Prime: timestamps only, no copying)
# =============================================================================
def _guide(title: str, runtime_min: float, target_minutes: float, spoiler_mode: str, language: str,
           offset_sec: float, api_key: Optional[str]) -> Dict[str, Any]:
    key = (clean_movie_title(title).lower(), round(runtime_min, 1), target_minutes, spoiler_mode, language,
           round(offset_sec), bool(api_key))
    if key in WATCH_GUIDE_CACHE:
        return WATCH_GUIDE_CACHE[key]
    try:
        guide = build_watch_guide(title, runtime_min * 60.0, target_minutes, spoiler_mode, language, api_key, offset_sec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    guide["text"] = guide_to_text(guide)
    WATCH_GUIDE_CACHE[key] = guide
    return guide


@app.get("/api/watchguide")
def watch_guide_get(title: str, runtime_min: float, target_minutes: float = 15, spoiler_mode: str = "full_cut",
                    language: str = "English", offset_sec: float = 0.0):
    if not 10 < runtime_min <= 600:
        raise HTTPException(status_code=400, detail="runtime_min must be between 10 and 600.")
    return _guide(title, runtime_min, max(3.0, min(120.0, target_minutes)), spoiler_mode, language, offset_sec, None)


@app.post("/api/watchguide")
def watch_guide_post(req: WatchGuideRequest):
    return _guide(req.title, req.runtime_min, req.target_minutes, req.spoiler_mode, req.language, req.offset_sec,
                  req.gemini_api_key)


@app.post("/api/watchguide/audio")
async def watch_guide_audio(req: WatchGuideRequest):
    guide = _guide(req.title, req.runtime_min, req.target_minutes, req.spoiler_mode, req.language, req.offset_sec,
                   req.gemini_api_key)
    text = f"{guide['title']}. {guide['story_recap']}".strip()
    voice = voice_for_text(text, req.voice if req.voice in AVAILABLE_VOICES else default_voice_for_language(req.language))
    out_dir = TEMP_DIR / "watch_guides"
    path = tts_cache_path(out_dir, text, voice)
    if not path.exists():
        try:
            await synthesize_voiceover_async(text, voice, str(path))
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    return {"audio_url": f"/api/watchguide/audio/{path.name}", "voice": voice, "characters": len(text)}


@app.get("/api/watchguide/audio/{name}")
def watch_guide_audio_file(name: str):
    path = safe_child(TEMP_DIR, "watch_guides", name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found.")
    return FileResponse(str(path), media_type="audio/mpeg")


# =============================================================================
# STORAGE MAINTENANCE
# =============================================================================
PROTECTED_TEMP_DIRS = {"downloads", "uploads", "tv_thumbs", "watch_guides", "transcripts", "voice_tests", "analysis_cache"}


def _size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


@app.get("/api/maintenance/storage")
def storage_usage():
    groups = {"downloads": TEMP_DIR / "downloads", "uploads": TEMP_DIR / "uploads"}
    sizes = {k: _size_of(v) if v.exists() else 0 for k, v in groups.items()}
    workspaces = [d for d in TEMP_DIR.iterdir() if d.is_dir() and d.name not in PROTECTED_TEMP_DIRS]
    sizes["job_workspaces"] = sum(_size_of(d) for d in workspaces)
    sizes["output_videos"] = _size_of(OUTPUT_DIR) if OUTPUT_DIR.exists() else 0
    return {"bytes": sizes, "formatted": {k: _gb(v) for k, v in sizes.items()}, "workspace_count": len(workspaces),
            "temp_dir": str(TEMP_DIR), "output_dir": str(OUTPUT_DIR)}


@app.post("/api/maintenance/cleanup")
def cleanup_storage(req: CleanupRequest):
    """Deletes old temporary workspaces (and optionally old downloads/uploads). Never touches output videos."""
    cutoff = time.time() - req.older_than_days * 86400
    busy = {jid for jid, j in JOBS.items()}
    freed, removed = 0, 0
    targets = [d for d in TEMP_DIR.iterdir() if d.is_dir() and d.name not in PROTECTED_TEMP_DIRS and d.name not in busy]
    for sub, flag in (("downloads", req.include_downloads), ("uploads", req.include_uploads)):
        if flag and (TEMP_DIR / sub).exists():
            targets += list((TEMP_DIR / sub).iterdir())
    for t in targets:
        try:
            if t.stat().st_mtime > cutoff:
                continue
            size = _size_of(t)
            if t.is_dir():
                shutil.rmtree(t)
            else:
                t.unlink()
            freed += size
            removed += 1
        except OSError:
            continue
    return {"success": True, "removed_items": removed, "freed_bytes": freed, "freed_formatted": _gb(freed)}


# =============================================================================
# SMART TV
# =============================================================================
# =============================================================================
# TERMS, RIGHTS CHECK, FREE LIBRARY, PRIVATE VIEWING, OWN-WORDS EXPLAINERS
# =============================================================================
class TermsAcceptRequest(BaseModel):
    version: str
    agree: bool = False


class RightsCheckRequest(BaseModel):
    url: str


class ExplainerRequest(BaseModel):
    source: str = "gutenberg"            # gutenberg | text | file | job
    gutenberg_id: Optional[int] = None
    text: Optional[str] = Field(None, max_length=3_000_000)
    file_path: Optional[str] = None
    job_id: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    kind: str = "book"                   # book | lecture | story
    target_minutes: float = Field(10, ge=2, le=40)
    language: str = "English"
    voice: Optional[str] = None
    render: bool = True
    rights_basis: Optional[str] = None   # own | permission (for text and files)
    force_private: bool = False


@app.get("/terms")
def terms_page():
    import html as _html

    def val(name: str, label: str) -> str:
        v = os.environ.get(name, "").strip()
        return _html.escape(v) if v else f'<span class="fill">[{label}: set {name} in .env]</span>'
    values = {"VERSION": TERMS_VERSION, "OPERATOR": val("CINECUT_OPERATOR", "operator name"),
              "EMAIL": val("CINECUT_CONTACT_EMAIL", "contact email"), "GRIEVANCE": val("CINECUT_GRIEVANCE_OFFICER", "grievance officer"),
              "CITY": val("CINECUT_JURISDICTION_CITY", "city"), "TTL": str(privacy.TTL_SEC // 60),
              "COUNTRY": {"IN": "India", "US": "United States"}.get(rights.COUNTRY, rights.COUNTRY)}
    page = (FRONTEND_DIR / "terms.html").read_text(encoding="utf-8")
    for k, v in values.items():
        page = page.replace("{{" + k + "}}", v)
    return HTMLResponse(page)


@app.get("/api/terms/status")
def terms_status():
    return {"version": TERMS_VERSION, "accepted": terms_accepted(), "url": "/terms", "country": rights.COUNTRY,
            "private_ttl_min": privacy.TTL_SEC // 60}


@app.post("/api/terms/accept")
def accept_terms(req: TermsAcceptRequest):
    if not req.agree or req.version != TERMS_VERSION:
        raise HTTPException(status_code=400, detail="Tick 'I agree' to accept the current Terms.")
    TERMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TERMS_FILE.write_text(json.dumps({"version": TERMS_VERSION, "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    return {"accepted": True, "version": TERMS_VERSION}


@app.post("/api/rights/check")
def rights_check(req: RightsCheckRequest):
    try:
        return rights.check_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"The licence could not be checked: {e}")


@app.get("/api/library/search")
def library_search(q: str, kind: str = "all", limit: int = 8):
    return library.search(q, kind, max(1, min(20, limit)))


@app.post("/api/private/{item_id}/delete")
def delete_private(item_id: str):
    """Deletes a private-viewing summary now (the source CineCut downloaded, transcript, analysis, narration, video)."""
    if not ID_RE.match(item_id or ""):
        raise HTTPException(status_code=404, detail="Nothing to delete.")
    item = JOBS.get(item_id) or EXPLAINERS.get(item_id)
    if item is None:
        if item_id in privacy.DELETED or privacy.is_registered(item_id):
            return privacy.purge(item_id, [JOBS, EXPLAINERS, TASKS], "deleted by you")
        raise HTTPException(status_code=404, detail="Nothing to delete.")
    if not item.get("private"):
        raise HTTPException(status_code=400, detail="Only private-viewing summaries are deleted here. Use Storage for other files.")
    if item.get("status") in ("analyzing", "rendering", "running"):
        raise HTTPException(status_code=409, detail="Wait for the current step to finish, then delete.")
    return privacy.purge(item_id, [JOBS, EXPLAINERS, TASKS], "deleted by you")


def _explainer_source(req: ExplainerRequest):
    """(source text and credits, rights decision) for an own-words explainer."""
    if req.source == "gutenberg":
        if not req.gutenberg_id:
            raise ValueError("Choose a Project Gutenberg book.")
        report = rights.check_gutenberg(req.gutenberg_id)
        decision = rights.decide(report, None, req.force_private)
        text = explainer_mod.gutenberg_text(report["text_url"] if report.get("text_url") else
                                            f"https://www.gutenberg.org/cache/epub/{req.gutenberg_id}/pg{req.gutenberg_id}.txt")
        return ({"title": req.title or report["title"], "author": req.author or report.get("creator", ""), "text": text,
                 "attribution": report["attribution"], "origin": report["url"], "license_label": report["license"]["label"]}, decision)
    if req.source == "job":
        job = get_job(req.job_id or "")
        if not job.get("subtitles"):
            raise ValueError("This video has no transcript to learn from. Transcribe it first.")
        decision = dict(job.get("rights") or rights.decide(None, None))
        return ({"title": req.title or film_title(job), "author": req.author or "", "text": explainer_mod.transcript_text(job["subtitles"]),
                 "attribution": decision.get("attribution") or "", "origin": job.get("video_path"),
                 "license_label": (decision.get("license") or {}).get("label") if decision.get("status") == "cleared" else None}, decision)
    if req.source == "file":
        path = clean_user_path(req.file_path)
        if not os.path.isfile(path) or Path(path).suffix.lower() not in (".txt", ".md", ".pdf", ".srt", ".vtt"):
            raise ValueError("Give the full path of a .txt, .md, .pdf or .srt file.")
        text = explainer_mod.file_text(path)
    else:
        text = req.text or ""
    if len(text.split()) < 150:
        raise ValueError("Give at least 150 words of text.")
    if req.rights_basis in ("own", "permission"):
        rights.record_declaration(req.file_path or "pasted text", req.rights_basis, None, TERMS_VERSION)
    decision = rights.decide(None, req.rights_basis, req.force_private)
    return ({"title": req.title or (Path(req.file_path).stem if req.file_path else "Untitled"), "author": req.author or "",
             "text": text, "attribution": "", "origin": "text provided by the user", "license_label": None}, decision)


@app.post("/api/explainer")
def start_explainer(req: ExplainerRequest):
    """Own-words explainer of a book, lecture or story, in the background (poll /api/task/{task_id})."""
    require_terms()
    if req.source not in ("gutenberg", "text", "file", "job"):
        raise HTTPException(status_code=400, detail="Unknown source.")
    if req.source == "job":
        get_job(req.job_id or "")
    eid = f"ex_{uuid.uuid4().hex[:8]}"
    task = {"task_id": eid, "kind": "explainer", "status": "running", "progress": 0.0, "message": "Getting the text...",
            "result": None, "error": None, "private": False}
    TASKS[eid] = EXPLAINERS[eid] = task

    def run():
        try:
            src, decision = _explainer_source(req)
            private = decision.get("status") == "private"
            task.update({"rights": decision, "private": private})
            work = TEMP_DIR / eid
            if private:
                privacy.register(eid, [str(work)])
                out_dir = work / "private"
            else:
                slug = re.sub(r"[^A-Za-z0-9]+", "_", src["title"]).strip("_")[:40] or "explainer"
                out_dir = OUTPUT_DIR / "explainers" / f"{slug}_{eid[3:]}"
            with privacy.private_scope(private):
                res = explainer_mod.make_explainer(src, req.kind, req.target_minutes, req.language, req.voice, work / "work",
                                                   out_dir, req.render, effective_key(),
                                                   progress=lambda p, m: task.update({"progress": round(p, 1), "message": m}))
            res.update({"rights": decision, "private": private, "explainer_id": eid,
                        "video_url": f"/api/explainer/{eid}/video" if res["files"].get("video") else None})
            if private:
                res["files"] = {k: v for k, v in res["files"].items() if k == "video"}
            task.update({"status": "done", "progress": 100.0, "message": "Explainer ready.", "result": res})
        except HTTPException as e:
            task.update({"status": "error", "error": e.detail, "message": e.detail})
        except Exception as e:
            task.update({"status": "error", "error": str(e), "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": eid}


def _finished_explainer(eid: str) -> Dict[str, Any]:
    t = EXPLAINERS.get(eid) if ID_RE.match(eid or "") else None
    if not t or t.get("status") != "done":
        if eid in privacy.DELETED:
            raise HTTPException(status_code=410, detail="This private explainer has been deleted, as promised.")
        raise HTTPException(status_code=404, detail="Explainer not found.")
    _touch_private(eid, t)
    return t


@app.get("/api/explainer/{eid}/video")
def explainer_video(eid: str, download: bool = False):
    t = _finished_explainer(eid)
    f = (t["result"].get("files") or {}).get("video")
    if not f or not os.path.exists(f):
        raise HTTPException(status_code=404, detail="This explainer has no video.")
    if download:
        no_export(t)
    headers = {"Content-Disposition": f'attachment; filename="{Path(f).parent.name}.mp4"'} if download else None
    return FileResponse(f, media_type="video/mp4", headers=headers)


@app.get("/api/explainer/{eid}/file/{name}")
def explainer_file(eid: str, name: str):
    t = _finished_explainer(eid)
    no_export(t)
    f = (t["result"].get("files") or {}).get(name) if name in ("script", "notes", "srt") else None
    if not f or not os.path.exists(f):
        raise HTTPException(status_code=404, detail="File not found.")
    media = {"script": "text/markdown", "notes": "application/json", "srt": "text/plain"}[name]
    return FileResponse(f, media_type=media, filename=Path(f).name)


@app.on_event("startup")
def _private_viewing_housekeeping():
    n = privacy.purge_leftovers([JOBS, EXPLAINERS, TASKS])
    if n:
        print(f"Private viewing: deleted {n} summary(ies) left over from the last session.")
    privacy.start_sweeper([JOBS, EXPLAINERS, TASKS])
    if os.environ.get("CINECUT_LIBRARY_BUILDER", "1") != "0":     # writes library summaries in the background
        from backend.webapp import builder as library_builder
        library_builder.start()


@app.get("/tv")
def serve_tv_leanback():
    tv_path = FRONTEND_DIR / "tv.html"
    if not tv_path.exists():
        raise HTTPException(status_code=404, detail="TV interface not found.")
    return FileResponse(str(tv_path), media_type="text/html")


@app.get("/api/tv/network_info")
def get_tv_network_info():
    ip = _primary_lan_ip()
    return {"local_ip": ip, "port": SERVER_PORT, "lan_mode": LAN_MODE,
            "tv_url": f"http://{ip}:{SERVER_PORT}/tv", "web_url": f"http://{ip}:{SERVER_PORT}",
            "how_to_enable": "TV access is off. Start CineCut with: python run.py --lan" if not LAN_MODE else ""}


@app.get("/api/tv/library")
def get_tv_library():
    items = []
    thumbs_dir = TEMP_DIR / "tv_thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    for f in OUTPUT_DIR.iterdir():
        if not (f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".mov")):
            continue
        duration = get_media_duration(str(f)) or 0.0
        thumb_name = re.sub(r"[^A-Za-z0-9_\-]", "_", f.stem)[:80] + "_thumb.jpg"
        thumb_path = thumbs_dir / thumb_name
        if not thumb_path.exists():
            extract_thumbnail_frame(str(f), min(duration / 2.0, 30.0) if duration > 10 else 1.0, str(thumb_path), width=480)
        items.append({"filename": f.name, "title": f.stem.replace("_", " ").replace("-", " ").strip(),
                      "size_mb": round(f.stat().st_size / 1048576, 1), "duration_sec": round(duration, 2),
                      "duration_formatted": format_seconds(duration) if duration else "Video",
                      "modified_time": f.stat().st_mtime, "stream_url": f"/api/tv/stream/{quote(f.name)}",
                      "thumbnail_url": f"/api/tv/thumb/{thumb_name}" if thumb_path.exists() else None})
    items.sort(key=lambda x: x["modified_time"], reverse=True)
    return {"items": items}


@app.get("/api/tv/thumb/{filename}")
def serve_tv_thumbnail(filename: str):
    path = safe_child(TEMP_DIR, "tv_thumbs", filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(str(path), media_type="image/jpeg")


@app.get("/api/tv/stream/{filename}")
def stream_tv_video(filename: str):
    path = safe_child(OUTPUT_DIR, filename)
    if not path.exists() or path.suffix.lower() not in (".mp4", ".mkv", ".mov"):
        raise HTTPException(status_code=404, detail="Video file not found.")
    return FileResponse(str(path), media_type="video/mp4")


from backend.webapp.routes import router as webapp_router  # noqa: E402  (web app, partner API, library tools)
from backend.webapp.youtube_routes import router as youtube_router  # noqa: E402  (YouTube packs, studio only)
app.include_router(webapp_router)
app.include_router(youtube_router)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
