"""
Lets the AI look at the scenes that have little or no dialogue (songs, fights, silent moments).

Low-resolution copies of those stretches (1 frame per second, with sound) are sent to Gemini's free tier, a few
clips per request. Gemini describes what happens on screen and tags each clip (song, action, romance, ...).
Results are cached per video and used by the story finder, by the narration for cut-out parts without dialogue,
and by the Songs / Action / Romance / Emotional filters.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from backend.config import TEMP_DIR, FFMPEG_BIN, GEMINI_API_BASE
from backend.video_engine import llm_usage
from backend.video_engine import gemini_client as gc
from backend.video_engine.audio_analyzer import EnergyIndex
from backend.video_engine.flavor_detector import ANY_TAG
from backend.video_engine.probe import format_seconds

CACHE_DIR = TEMP_DIR / "visual_cache"
CACHE_VERSION = "v1"
VISION_MODELS = ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest", "gemini-3.8-flash",
                 "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash-lite")
ALLOWED_TAGS = {"song", "action", "romance", "comedy", "emotional", "villain", "hero"}
TAG_LABELS = {"song": "Song sequence", "action": "Action sequence", "romance": "Romantic scene", "comedy": "Comedy scene",
              "emotional": "Emotional scene", "villain": "Villain scene", "hero": "Hero moment"}
CLIP_SEC = 36.0
PER_REQUEST = 4

PROMPT = """You are helping condense the {kind} "{title}". Below are {n} short low-resolution clips from it (one frame
per second, with sound). For each clip, in English:
- "description": one or two short sentences on what happens on screen: who does what, where, and the mood. Describe
  people by their role or appearance unless their name is shown or spoken in the clip. Never guess actors' real names.
- "tags": any that apply from: song, action, romance, comedy, emotional, villain, hero
Return JSON: {{"clips": [{{"clip": 1, "description": "...", "tags": ["song"]}}]}}"""


def _signature(video_path: str, max_windows: int) -> str:
    st = os.stat(video_path)
    raw = f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}|{CACHE_VERSION}|{max_windows}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def pick_windows(subs: List[Dict[str, Any]], energy_values: List[float], shot_cuts: List[float], duration: float,
                 max_windows: int = 16, quiet_words: int = 12) -> List[Tuple[float, float]]:
    """Minutes with few spoken words but real sound or picture activity, spread across the film."""
    minute = 60.0
    n = int(duration // minute)
    if n < 3:
        return []
    words = [0] * (n + 1)
    for s in subs or []:
        text = ANY_TAG.sub(" ", s.get("text", "")).strip()
        if text:
            words[min(n, int(float(s["start"]) // minute))] += len(text.split())
    energy = EnergyIndex(energy_values or [])
    cands: List[Tuple[float, float, float]] = []
    for i in range(n):
        a, b = i * minute, (i + 1) * minute
        if a < 60.0 or b > duration - 120.0:      # opening logos and end credits
            continue
        if subs and words[i] > quiet_words:
            continue
        loud = energy.percentile(a, b) if energy_values else 50.0
        if loud < 15.0:                           # near silence: little to see or hear
            continue
        cuts = sum(1 for c in shot_cuts or [] if a <= c < b)
        cands.append((loud + 3.0 * min(cuts, 10), a, b))
    if not cands:
        return []
    region = duration / max_windows
    chosen: Dict[int, Tuple[float, float, float]] = {}
    for c in cands:
        r = int(c[1] // region)
        if r not in chosen or c[0] > chosen[r][0]:
            chosen[r] = c
    picked = list(chosen.values())
    for c in sorted(cands, key=lambda x: -x[0]):
        if len(picked) >= max_windows:
            break
        if c not in picked:
            picked.append(c)
    picked = sorted(picked, key=lambda x: x[1])[:max_windows]
    out = []
    for _, a, b in picked:
        start = energy.loudest_window(a, b, CLIP_SEC) if energy_values else a
        start = max(a, min(start, b - CLIP_SEC))
        out.append((round(start, 1), round(start + CLIP_SEC, 1)))
    return out


def _encode_clip(src: str, start: float, out: Path) -> bool:
    cmd = [FFMPEG_BIN, "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{CLIP_SEC:.2f}", "-i", src,
           "-vf", "fps=1,scale=320:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "34",
           "-c:a", "aac", "-b:a", "24k", "-ac", "1", "-ar", "16000", "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    return res.returncode == 0 and out.exists() and out.stat().st_size > 1000


def ask_gemini(parts: List[Dict[str, Any]], api_key: Optional[str] = None, timeout: float = 240.0) -> Any:
    """Sends text + inline video parts to the first free Gemini model that accepts them (all keys, all models)."""
    last = "no Gemini key in .env"
    for key in gc.all_keys(api_key):
        if not llm_usage.available("gemini", key):
            continue
        kid = llm_usage.key_id("gemini", key)
        for model in VISION_MODELS:
            slot = f"{kid}|{model}"
            if gc._blocked.get(slot, 0) > time.time() or not llm_usage.available("gemini", key, model):
                continue
            payload = {"contents": [{"role": "user", "parts": parts}],
                       "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                                            "mediaResolution": "MEDIA_RESOLUTION_LOW"}}
            try:
                res = httpx.post(f"{GEMINI_API_BASE}/{model}:generateContent", json=payload,
                                 headers={"x-goog-api-key": key}, timeout=timeout)
            except httpx.HTTPError as e:
                last = str(e)
                continue
            body = res.text[:1500]
            if res.status_code == 429 or "RESOURCE_EXHAUSTED" in body:
                llm_usage.note_429("gemini", key, model, body, gc._retry_after(res))
                last = gc._error_message(res)
                continue
            if res.status_code in (500, 502, 503, 504):
                gc._blocked[slot] = time.time() + 20
                last = gc._error_message(res)
                continue
            if res.status_code in (400, 404) and any(x in body.lower() for x in ("not found", "not supported", "not enabled")):
                gc._blocked[slot] = time.time() + 24 * 3600
                last = gc._error_message(res)
                continue
            if res.status_code != 200:
                last = gc._error_message(res)
                continue
            data = res.json()
            out_parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            llm_usage.record("gemini", key, model=model)
            return gc._parse_loose("".join(p.get("text", "") for p in out_parts if not p.get("thought")))
    raise RuntimeError(f"No free Gemini model could look at the clips ({last[:160]})")


def describe_scenes(video_path: str, title: str, subs: List[Dict[str, Any]], energy_values: List[float],
                    shot_cuts: List[float], duration: float, api_key: Optional[str] = None, kind: str = "film",
                    progress: Optional[Callable[[float, str], None]] = None, max_windows: int = 16) -> List[Dict[str, Any]]:
    """[{start, end, description, tags}] for the low-dialogue stretches of the video (cached)."""
    if not gc.all_keys(api_key) or duration < 600:
        return []
    sig = _signature(video_path, max_windows)
    cache = CACHE_DIR / f"{sig}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    windows = pick_windows(subs, energy_values, shot_cuts, duration, max_windows)
    if not windows:
        return []
    work = CACHE_DIR / sig
    work.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda p, m: None)
    clips = []
    for i, (s, e) in enumerate(windows):
        report(100.0 * i / (len(windows) * 2), f"Preparing scene {i + 1} of {len(windows)} for the AI to look at...")
        out = work / f"clip_{i:02d}.mp4"
        if _encode_clip(video_path, s, out):
            clips.append((s, e, out))
    results: List[Dict[str, Any]] = []
    try:
        for g in range(0, len(clips), PER_REQUEST):
            group = clips[g:g + PER_REQUEST]
            report(50.0 + 50.0 * g / max(1, len(clips)), f"The AI is looking at scenes {g + 1}-{g + len(group)} of {len(clips)}...")
            parts: List[Dict[str, Any]] = [{"text": PROMPT.format(kind=kind, title=title, n=len(group))}]
            for k, (s, e, out) in enumerate(group, 1):
                parts.append({"text": f"Clip {k} (film time {format_seconds(s)} to {format_seconds(e)}):"})
                parts.append({"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(out.read_bytes()).decode()}})
            try:
                data = ask_gemini(parts, api_key)
            except Exception as ex:
                print(f"Scene viewing failed for clips {g + 1}-{g + len(group)}: {ex}")
                continue
            items = data.get("clips") if isinstance(data, dict) else data if isinstance(data, list) else []
            for item in items or []:
                try:
                    idx = int(item.get("clip", 0)) - 1
                except (TypeError, ValueError, AttributeError):
                    continue
                if not 0 <= idx < len(group):
                    continue
                s, e, _ = group[idx]
                desc = " ".join(str(item.get("description", "")).split())[:320]
                tags = [t for t in (str(x).lower().strip() for x in (item.get("tags") or [])) if t in ALLOWED_TAGS]
                if desc:
                    results.append({"start": s, "end": e, "description": desc, "tags": tags})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    results.sort(key=lambda x: x["start"])
    from backend.video_engine import privacy
    if results and privacy.cache_allowed():       # private viewing leaves nothing behind
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    return results


def visual_segments(visuals: List[Dict[str, Any]], tag: str) -> List[Dict[str, Any]]:
    """Scenes the AI saw with this tag, in the same shape as the sound/caption detectors' segments."""
    return [{"start": v["start"], "end": v["end"], "score": 85.0, "flavor": tag, "label": TAG_LABELS.get(tag, "Scene"),
             "evidence": "seen on screen by the AI"} for v in visuals or [] if tag in (v.get("tags") or [])]


def visuals_between(visuals: List[Dict[str, Any]], a: float, b: float) -> List[Dict[str, Any]]:
    return [v for v in visuals or [] if v["start"] >= a - 1 and v["end"] <= b + 1]
