import re
from pathlib import Path
from typing import List, Dict, Any, Optional

_TIME = r"(?:\d{1,2}:)?\d{1,2}:\d{2}[,\.]\d{1,3}"
_CUE_RE = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})")
_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")


def parse_srt_time(time_str: str) -> float:
    """Convert '01:23:45,678', '01:23:45.678', '23:45.678' or '01:23:45' to seconds."""
    time_str = time_str.strip().replace(",", ".")
    parts = time_str.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(time_str)
    except ValueError:
        return 0.0


def parse_srt_file(file_path: str) -> List[Dict[str, Any]]:
    """Parse an SRT or WebVTT file into timestamped dialogue entries."""
    path = Path(file_path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    content = None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        content = raw.decode("utf-16", errors="replace")
    else:
        for enc in ("utf-8-sig", "cp1252"):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    if content is None:
        content = raw.decode("latin-1", errors="replace")
    return parse_srt_string(content)


def parse_srt_string(content: str) -> List[Dict[str, Any]]:
    """
    Block-based SRT/VTT parser. Handles VTT headers and cue settings, strips styling tags, and
    removes YouTube auto-caption "rolling" duplicates (each cue repeats the previous line).
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    entries: List[Dict[str, Any]] = []
    prev_lines: set = set()

    for block in re.split(r"\n\s*\n", content):
        lines = [l for l in block.split("\n") if l.strip()]
        cue_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if cue_idx is None:
            continue
        m = _CUE_RE.match(lines[cue_idx])
        if not m:
            continue
        start, end = parse_srt_time(m.group(1)), parse_srt_time(m.group(2))
        if end - start < 0.05:
            # YouTube auto-captions insert 10 ms "hold" cues that only repeat text
            continue
        text_lines = []
        for l in lines[cue_idx + 1:]:
            cleaned = " ".join(_TAG_RE.sub("", l).split())
            if cleaned:
                text_lines.append(cleaned)
        fresh = [l for l in text_lines if l not in prev_lines]
        prev_lines = set(text_lines)
        if not fresh:
            if entries:
                entries[-1]["end"] = max(entries[-1]["end"], end)
                entries[-1]["duration"] = entries[-1]["end"] - entries[-1]["start"]
            continue
        text = " ".join(fresh)
        if entries and entries[-1]["text"] == text:
            entries[-1]["end"] = max(entries[-1]["end"], end)
            entries[-1]["duration"] = entries[-1]["end"] - entries[-1]["start"]
            continue
        entries.append({"start": start, "end": end, "duration": end - start, "text": text})

    entries.sort(key=lambda e: e["start"])
    return entries


def extract_character_roster(subtitles: List[Dict[str, Any]], min_appearances: int = 2) -> List[Dict[str, Any]]:
    """Finds speaker labels such as 'CARTER: ...', '[Batman] ...', '(Agent Smith) ...'."""
    speaker_counts: Dict[str, int] = {}
    pattern = re.compile(r"^([A-Z][A-Za-z0-9\.\s_]{2,20}):|^\[([A-Za-z0-9\.\s_]{2,20})\]|^\(([A-Za-z0-9\.\s_]{2,20})\)")
    ignore = {"MUSIC", "SOUND", "LAUGHTER", "APPLAUSE", "SCREAM", "SIGHS", "GRUNTS", "MAN", "WOMAN", "CROWD"}
    for sub in subtitles:
        m = pattern.search(sub.get("text", ""))
        if m:
            name = (m.group(1) or m.group(2) or m.group(3)).strip()
            if name.upper() not in ignore:
                speaker_counts[name] = speaker_counts.get(name, 0) + 1
    roster = [{"name": n, "count": c} for n, c in speaker_counts.items() if c >= min_appearances]
    roster.sort(key=lambda x: x["count"], reverse=True)
    return roster


def group_dialogue_into_scenes(subtitles: List[Dict[str, Any]], max_gap_sec: float = 6.0,
                               min_scene_sec: float = 15.0, max_scene_sec: float = 120.0) -> List[Dict[str, Any]]:
    """Cluster consecutive dialogue entries into coherent scenes of at most `max_scene_sec` (subtitles that run
    back to back, common in auto-captions, would otherwise merge a whole film into one scene)."""
    if not subtitles:
        return []

    def make_scene(subs):
        s0, s1 = max(0.0, subs[0]["start"] - 0.5), subs[-1]["end"] + 0.5
        return {"start": s0, "end": s1, "duration": s1 - s0,
                "dialogue": " ".join(s["text"] for s in subs), "sub_count": len(subs)}

    scenes, current = [], [subtitles[0]]
    for sub in subtitles[1:]:
        if sub["start"] - current[-1]["end"] > max_gap_sec or sub["end"] - current[0]["start"] > max_scene_sec:
            scenes.append(make_scene(current))
            current = [sub]
        else:
            current.append(sub)
    scenes.append(make_scene(current))

    merged: List[Dict[str, Any]] = []
    for s in scenes:
        if merged and s["start"] - merged[-1]["end"] < 3.0 and s["end"] - merged[-1]["start"] <= max_scene_sec:
            merged[-1]["end"] = s["end"]
            merged[-1]["duration"] = merged[-1]["end"] - merged[-1]["start"]
            merged[-1]["dialogue"] += " " + s["dialogue"]
            merged[-1]["sub_count"] += s["sub_count"]
        else:
            merged.append(s)
    return merged


def score_dialogue_significance(dialogue: str, duration: float = 0.0, target_character: Optional[str] = None) -> float:
    """
    Heuristic narrative score (0-100). `duration` is optional; when missing it is estimated
    from the word count at a normal speaking rate.
    """
    if not dialogue:
        return 0.0
    words = dialogue.split()
    word_count = len(words)
    if not duration or duration <= 0:
        duration = max(1.0, word_count / 2.5)
    wps = word_count / max(duration, 1.0)

    plot_keywords = {
        "why", "what", "kill", "die", "death", "love", "hate", "truth", "lie",
        "secret", "save", "run", "stop", "police", "help", "danger", "find",
        "know", "remember", "never", "always", "plan", "money", "war", "blood",
        "father", "mother", "family", "promise", "betray", "escape", "time"
    }
    lower = dialogue.lower()
    keyword_hits = sum(1 for kw in plot_keywords if re.search(rf"\b{kw}\b", lower))
    questions, exclamations = dialogue.count("?"), dialogue.count("!")
    caps_words = sum(1 for w in words if len(w) > 1 and w.isupper())

    density_score = min(wps / 2.0, 1.0) * 35.0
    keyword_score = min(keyword_hits * 5.0, 25.0)
    punc_score = min((questions * 4.0) + (exclamations * 3.0) + (caps_words * 3.0), 20.0)

    character_bonus = 0.0
    if target_character:
        character_bonus = 35.0 if target_character.lower() in lower else -20.0

    return min(100.0, max(5.0, density_score + keyword_score + punc_score + character_bonus))


def format_script_for_llm(subtitles: List[Dict[str, Any]], max_chars: int = 400000) -> str:
    """Formats dialogue as a timestamped transcript for Gemini."""
    from backend.video_engine.probe import format_seconds
    script = "\n".join(f"[{format_seconds(s['start'])}] {s['text']}" for s in subtitles)
    if len(script) > max_chars:
        return script[:max_chars] + "\n...[Transcript truncated due to length]..."
    return script
