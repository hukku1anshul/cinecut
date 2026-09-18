"""
Story beats found in the film's own dialogue.

Instead of placing beats at fixed percentages of the runtime, the AI reads a timestamped digest of the
subtitles/transcript and marks where the real setup, turning points, climax and resolution happen, with
start and end times taken from the transcript. Works with any free provider in llm_client (Gemini,
Groq, Cloudflare, NVIDIA, then the local Ollama model). Results are cached per video.
"""
import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from backend.config import TEMP_DIR
from backend.video_engine import llm_client
from backend.video_engine.flavor_detector import ANY_TAG
from backend.video_engine.probe import format_seconds
from backend.video_engine.subtitle_parser import parse_srt_time

CACHE_DIR = TEMP_DIR / "story_cache"
CACHE_VERSION = "b6"
ALLOWED_TAGS = {"action", "song", "comedy", "emotional", "romance", "hero", "villain", "twist"}

PROMPT = """You are a film editor preparing condensed versions of the film "{title}" ({minutes} minutes long),
anywhere from a 5-minute to a 45-minute version. Below is its timestamped dialogue, one line per {block} seconds of film.

List the film's story beats in chronological order, 18 to 24 of them, covering the whole film from the opening to the end:
- tier 1: the 7 to 10 core beats a first-time viewer needs to follow the story (setup and main characters, inciting
  incident, key turns and revelations, midpoint, crisis, climax, resolution)
- tier 2: supporting beats that make the story richer (subplots, relationships, obstacles)
- tier 3: memorable extra moments (songs, comic scenes, action set pieces, emotional moments)
Use ONLY what the dialogue shows.
For each beat give:
- "start" and "end": times copied from the transcript timestamps (HH:MM:SS), 1 to 4 minutes apart
- "title": a short scene title
- "description": one factual sentence about what happens in that beat
- "tier": 1, 2 or 3
- "importance": 0 to 100
- "tags": any that apply from: action, song, comedy, emotional, romance, hero, villain, twist
- "characters": names of the people in the beat, written in English letters (e.g. "Raj", not Devanagari)
Write titles, descriptions and names in English, even when the dialogue is in another language.
Beats must not overlap.

Transcript:
---
{digest}
---
Return JSON: {{"theme": "one sentence about what the film is about", "main_characters": [{{"name": "name in English letters", "role": "hero, villain, lead or supporting"}}], "beats": [{{"start": "00:12:30", "end": "00:14:10", "title": "...", "description": "...", "tier": 1, "importance": 90, "tags": ["comedy"], "characters": ["..."]}}]}}"""


def transcript_digest(subtitles: List[Dict[str, Any]], duration: float, max_chars: int = 22000) -> (str, int):
    block = max(60, int(duration / 110))
    while True:
        rows: Dict[int, List[str]] = {}
        for s in subtitles:
            text = " ".join(ANY_TAG.sub(" ", s.get("text", "")).split())
            if text:
                rows.setdefault(int(s["start"] // block), []).append(text)
        per_line = max(80, max_chars // max(1, len(rows)))
        lines = [f"[{format_seconds(k * block)}] {' '.join(v)[:per_line]}" for k, v in sorted(rows.items())]
        digest = "\n".join(lines)
        if len(digest) <= max_chars or block > 600:
            return digest, block
        block = int(block * 1.4)


def _cache_path(video_path: str, subtitles: List[Dict[str, Any]], extra: str = "") -> Optional[str]:
    try:
        st = os.stat(video_path)
    except OSError:
        return None
    sig = f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}|{len(subtitles)}|{CACHE_VERSION}|{extra}"
    return str(CACHE_DIR / (hashlib.sha1(sig.encode()).hexdigest()[:20] + ".json"))


def _act(pos: float) -> str:
    if pos < 0.25:
        return "Act 1"
    if pos < 0.5:
        return "Act 2A"
    if pos < 0.8:
        return "Act 2B"
    return "Act 3"


CHUNK_PROMPT = """You are reading the film "{title}" ({minutes} minutes long) part by part to find its story.
Below are {n} consecutive parts of its timestamped dialogue{visual_note}.
For each part, list the 2 to 5 events that matter to the story, in order. For each event give:
"start" and "end" (times copied from the timestamps, HH:MM:SS), "event" (one factual sentence; names in English letters),
"importance" (0-100, for the whole film's story), "tags" (any of: action, song, comedy, emotional, romance, hero, villain,
twist) and "characters" (names in English letters). Use only what the dialogue and scene descriptions show.

{parts}

Return JSON: {{"events": [{{"start": "00:12:30", "end": "00:14:10", "event": "...", "importance": 80, "tags": [], "characters": []}}]}}"""

GLOBAL_PROMPT = """You are a film editor preparing condensed versions of the film "{title}" ({minutes} minutes long),
anywhere from a 5-minute to a 45-minute version. Below is every story event found in the film, in order.

Choose exactly {n} story beats (you may merge neighbouring events into one beat), covering the whole film from the
opening to the end:
- tier 1: about {n1} core beats a first-time viewer needs (setup and main characters, inciting incident, key turns
  and revelations, midpoint, crisis, climax, resolution)
- tier 2: about {n2} supporting beats (subplots, relationships, obstacles)
- tier 3: about {n3} memorable extra moments (songs, comic scenes, action set pieces, emotional moments)
For each beat give "start" and "end" copied from the events' times (1 to 4 minutes apart), "title", "description" (one
factual sentence), "tier" (1, 2 or 3), "importance" (0-100), "tags" (from: action, song, comedy, emotional, romance,
hero, villain, twist) and "characters" (names in English letters). Beats must be in order and must not overlap.
Write titles, descriptions and names in English.{retry}

Events:
{events}

Return JSON: {{"theme": "one sentence about what the film is about", "main_characters": [{{"name": "name in English letters", "role": "hero, villain, lead or supporting"}}], "beats": [{{"start": "00:12:30", "end": "00:14:10", "title": "...", "description": "...", "tier": 1, "importance": 90, "tags": ["comedy"], "characters": ["..."]}}]}}"""


def beats_for_runtime(duration: float) -> int:
    """About one story beat per 7 minutes of film (10 to 28), so the count no longer depends on chance."""
    return max(10, min(28, int(round(duration / 60.0 / 7.0))))


def _range_digest(subs: List[Dict[str, Any]], a: float, b: float, visuals: Optional[List[Dict[str, Any]]],
                  block: int = 30, max_chars: int = 5200) -> str:
    rows: Dict[int, List[str]] = {}
    for s in subs:
        if a <= float(s["start"]) < b:
            text = " ".join(ANY_TAG.sub(" ", s.get("text", "")).split())
            if text:
                rows.setdefault(int(float(s["start"]) // block), []).append(text)
    for v in visuals or []:
        if a <= v["start"] < b:
            rows.setdefault(int(v["start"] // block), []).append(f"(on screen: {v['description']})")
    if not rows:
        return "(no dialogue)"
    per = max(60, max_chars // len(rows))
    return "\n".join(f"[{format_seconds(k * block)}] {' '.join(v)[:per]}" for k, v in sorted(rows.items()))


def _fill_story_gaps(beats: List[Dict[str, Any]], events: List[Dict[str, Any]], duration: float) -> int:
    """If a long stretch of the film has no beat (over 15 minutes or 10% of the runtime), adds the most important
    events found in that stretch as supporting beats, so no part of the story is silently dropped."""
    def t(b):
        try:
            return parse_srt_time(str(b.get("start", "")))
        except Exception:
            return None
    starts = sorted(x for x in (t(b) for b in beats) if x is not None)
    limit = max(900.0, duration * 0.10)
    bounds = [0.0] + starts + [duration]
    added = 0
    for a, b in zip(bounds, bounds[1:]):
        if b - a <= limit:
            continue
        inside = sorted([ev for ev in events if a + 60 <= ev["s"] <= b - 60], key=lambda ev: -ev["imp"])
        want = max(1, int((b - a) // limit))
        chosen = []
        for ev in inside:
            if len(chosen) >= want:
                break
            if all(abs(ev["s"] - c["s"]) > 240 for c in chosen):
                chosen.append(ev)
        for ev in chosen:
            words = ev["text"].split()
            beats.append({"start": format_seconds(ev["s"]), "end": format_seconds(min(ev["e"], ev["s"] + 180)),
                          "title": " ".join(words[:6]).rstrip(".,") or "Story event", "description": ev["text"],
                          "tier": 2, "importance": ev["imp"], "tags": ev["tags"], "characters": ev["chars"]})
            added += 1
    return added


def _hierarchical(title: str, subs: List[Dict[str, Any]], duration: float, n: int, api_key: Optional[str],
                  visuals: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Reads the film in 12-minute parts (three parts per request), then chooses the beats from all events."""
    chunk = 720
    ranges = [(float(t), float(min(duration, t + chunk))) for t in range(0, int(duration), chunk)]
    events: List[Dict[str, Any]] = []
    note = " and short descriptions of what is seen on screen where there is little dialogue" if visuals else ""
    for g in range(0, len(ranges), 3):
        group = ranges[g:g + 3]
        parts = "\n\n".join(f"Part {k} ({format_seconds(a)} to {format_seconds(b)}):\n{_range_digest(subs, a, b, visuals)}"
                            for k, (a, b) in enumerate(group, 1))
        try:
            data = llm_client.generate_json(CHUNK_PROMPT.format(title=title, minutes=int(duration // 60), n=len(group),
                                                                visual_note=note, parts=parts),
                                            api_key=api_key, temperature=0.1, timeout=180, num_ctx=16384)
        except Exception as e:
            print(f"Story events for part {g // 3 + 1} failed: {e}")
            continue
        for ev in (data.get("events") if isinstance(data, dict) else None) or []:
            try:
                s0, s1 = parse_srt_time(str(ev.get("start", ""))), parse_srt_time(str(ev.get("end", "")))
                imp = int(ev.get("importance", 60))
            except Exception:
                continue
            if not group[0][0] - 5 <= s0 <= group[-1][1] + 5:
                continue
            tags = [str(t).lower() for t in (ev.get("tags") or []) if str(t).lower() in ALLOWED_TAGS]
            chars = [str(c)[:40] for c in (ev.get("characters") or []) if isinstance(c, str)][:5]
            events.append({"s": s0, "e": max(s1, s0 + 30), "imp": imp, "text": str(ev.get("event", ""))[:220], "tags": tags, "chars": chars})
    if len(events) < max(6, n // 2):
        return None
    events.sort(key=lambda x: x["s"])
    listing = "\n".join(f"- [{format_seconds(ev['s'])}-{format_seconds(ev['e'])}] (importance {ev['imp']}) {ev['text']}"
                        + (f" {{{', '.join(ev['tags'])}}}" if ev["tags"] else "")
                        + (f" <{', '.join(ev['chars'])}>" if ev["chars"] else "") for ev in events)
    n1 = max(4, int(round(n * 0.4)))
    n2 = max(2, int(round(n * 0.35)))
    retry, data = "", None
    for _ in range(2):
        try:
            data = llm_client.generate_json(GLOBAL_PROMPT.format(title=title, minutes=int(duration // 60), n=n, n1=n1, n2=n2,
                                                                 n3=max(1, n - n1 - n2), retry=retry, events=listing[:30000]),
                                            api_key=api_key, temperature=0.1, timeout=240, num_ctx=16384)
        except Exception as e:
            print(f"Story beats from events failed: {e}")
            return None
        beats = data.get("beats") if isinstance(data, dict) else None
        if beats and len(beats) >= int(n * 0.7):
            data["_parts_read"] = len(ranges)
            data["_filled"] = _fill_story_gaps(beats, events, duration)
            return data
        retry = f"\\nYour previous answer had {len(beats or [])} beats; return exactly {n}."
    return data if isinstance(data, dict) else None


def ai_story_beats(title: str, subtitles: List[Dict[str, Any]], duration: float, video_path: str,
                   api_key: Optional[str] = None, visuals: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Essence dict (same shape as essence_engine) with beats located in the transcript, or None."""
    if len(subtitles) < 40 or duration < 600 or not llm_client.provider_for(api_key):
        return None
    target_n = beats_for_runtime(duration)
    cache = _cache_path(video_path, subtitles, f"{target_n}|{len(visuals or [])}")
    if cache and os.path.exists(cache):
        try:
            return json.loads(open(cache, encoding="utf-8").read())
        except Exception:
            pass
    data = _hierarchical(title, subtitles, duration, target_n, api_key, visuals)
    if not data:   # fall back to one pass over the whole transcript digest
        digest, block = transcript_digest(subtitles, duration)
        prompt = PROMPT.format(title=title, minutes=int(duration // 60), block=block, digest=digest)
        prompt = prompt.replace("18 to 24 of them", f"exactly {target_n} of them")
        try:
            data = llm_client.generate_json(prompt, api_key=api_key, temperature=0.1, timeout=240, num_ctx=16384)
        except Exception as e:
            print(f"AI story beats failed: {e}")
            return None
    beats = []
    for b in data.get("beats", []) if isinstance(data, dict) else []:
        try:
            start = parse_srt_time(str(b.get("start", "")))
            end = parse_srt_time(str(b.get("end", "")))
        except Exception:
            continue
        if end - start < 20:
            end = start + 90
        if start >= duration - 10 or start < 0:
            continue
        end = min(end, duration)
        try:
            importance = int(b.get("importance", 85))
        except (TypeError, ValueError):
            importance = 85
        try:
            tier = max(1, min(3, int(b.get("tier", 0) or 0)))
        except (TypeError, ValueError):
            tier = 0
        tags = [str(t).lower().strip() for t in (b.get("tags") or []) if isinstance(t, str)]
        chars = [str(c).strip()[:40] for c in (b.get("characters") or []) if isinstance(c, str) and str(c).strip()]
        beats.append({"title": str(b.get("title", "Story beat"))[:80], "description": str(b.get("description", ""))[:300],
                      "start_sec": round(start, 1), "end_sec": round(end, 1), "importance": max(10, min(100, importance)),
                      "tier": tier, "tags": [t for t in tags if t in ALLOWED_TAGS], "characters": chars[:6]})
    beats.sort(key=lambda x: x["start_sec"])
    cleaned: List[Dict[str, Any]] = []
    for b in beats:
        if cleaned and b["start_sec"] < cleaned[-1]["end_sec"]:
            continue
        cleaned.append(b)
    if len(cleaned) < 4:
        return None
    if not any(b["tier"] for b in cleaned):
        # the model gave no tiers: the most important half are core beats
        ranked = sorted(cleaned, key=lambda x: -x["importance"])
        for n, b in enumerate(ranked):
            b["tier"] = 1 if n < max(6, len(ranked) // 2) else 2
    for b in cleaned:
        b["tier"] = b["tier"] or 2
    milestones = []
    for i, b in enumerate(cleaned, 1):
        pos = b["start_sec"] / duration
        milestones.append({"id": i, "act": f"{_act(pos)}: {b['title']}", "title": b["title"], "description": b["description"],
                           "start_sec": b["start_sec"], "end_sec": b["end_sec"], "exact": False,
                           "target_time_sec": b["start_sec"], "target_percent": round(pos, 4),
                           "target_time_formatted": format_seconds(b["start_sec"]), "importance": b["importance"],
                           "tier": b["tier"], "tags": b["tags"], "characters": b["characters"]})
    writer = llm_client.last_used.get("provider", "AI")
    essence = {"source": "ai_transcript_beats", "movie_title": title, "essence_theme": str(data.get("theme", ""))[:300],
               "milestones": milestones, "timing": "transcript", "is_generic": False,
               "main_characters": [{"name": str(c.get("name", ""))[:40], "role": str(c.get("role", "")).lower()[:20]}
                                   for c in (data.get("main_characters") or []) if isinstance(c, dict) and c.get("name")][:12],
               "timing_note": (f"Story beats were located in this film's own dialogue ({writer}), read in "
                               f"{data.get('_parts_read')} parts and then as a whole"
                               + (f"; {data.get('_filled')} beats were added where a long stretch had none." if data.get("_filled") else ".")
                               if data.get("_parts_read")
                               else f"Story beats were located in this film's own dialogue ({writer}).")}
    from backend.video_engine import privacy
    if cache and privacy.cache_allowed():         # private viewing leaves nothing behind
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(essence, f, ensure_ascii=False)
    return essence


def merge_supporting_beats(primary: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Keeps hand-made database beats as the core and adds the transcript beats that fall between them as
    supporting beats, so long cuts of database films also show more of the story."""
    core = [dict(m, tier=1) for m in primary.get("milestones", [])]
    spans = []
    for m in core:
        a = float(m.get("start_sec", m.get("target_time_sec", 0.0)) or 0.0)
        b = float(m.get("end_sec", a + 60.0) or a + 60.0)
        spans.append((a - 90.0, b + 90.0))
    added = [dict(m, tier=max(2, int(m.get("tier", 2) or 2)), exact=False) for m in extra.get("milestones", [])
             if not any(lo <= float(m["start_sec"]) <= hi for lo, hi in spans)]
    out = dict(primary)
    merged = sorted(core + added, key=lambda m: float(m.get("target_time_sec", m.get("start_sec", 0.0)) or 0.0))
    for n, m in enumerate(merged, 1):
        m["id"] = n
    out["milestones"] = merged
    out["main_characters"] = extra.get("main_characters", [])
    if added:
        out["timing_note"] = (str(primary.get("timing_note", "")) +
                              f" {len(added)} supporting beats were added from this film's own dialogue.").strip()
    return out
