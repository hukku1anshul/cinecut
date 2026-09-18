"""
Streaming Watch Guide (Netflix / Prime Video / any official app).

CineCut cannot and should not copy video from Netflix or Prime Video: both use DRM, their terms of use
forbid copying, and bypassing DRM is illegal (US 17 U.S.C. 1201, India s.65A). What it CAN do legally is
tell you where the essential scenes are, so you watch the condensed story inside the official player:
"jump to 1:14:20, watch until 1:17:05, skip 6 minutes...". No footage is copied. The guide uses the same
essence engine and duration budget as the video cuts, plus your own-words narration between jumps.
"""
from typing import Dict, Any, Optional, List

from backend.video_engine.essence_engine import extract_film_canonical_essence
from backend.video_engine.summarizer import compute_local_heuristic_summary
from backend.video_engine.probe import format_seconds

ACCURACY_NOTES = {
    "exact": ("Timestamps were measured on one specific release. Streaming versions can start earlier or later "
              "(studio logos, recaps, regional edits). If the first jump lands in the wrong place, use the offset control."),
    "scaled": ("Timestamps were scaled from a reference release of a different length. Expect them to be off by a "
               "minute or two; use the offset control to line them up."),
    "percent": ("Scene positions are estimates from Gemini's knowledge of the plot, placed by typical story position. "
                "Treat them as approximate starting points."),
    "generic": ("This film is not in the essence database and no Gemini key was used, so the guide uses generic "
                "three-act positions, not this film's real scenes. Add a Gemini key for a film-specific guide.")
}


def build_watch_guide(title: str, runtime_sec: float, target_minutes: float = 15, spoiler_mode: str = "full_cut",
                      language: str = "English", gemini_api_key: Optional[str] = None,
                      offset_sec: float = 0.0) -> Dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("Enter the film title.")
    if runtime_sec < 600:
        raise ValueError("Enter the full runtime of the film (at least 10 minutes).")
    essence = extract_film_canonical_essence(title, runtime_sec, gemini_api_key)
    scenes = compute_local_heuristic_summary(
        video_duration_sec=runtime_sec, target_duration_sec=target_minutes * 60.0, candidate_scenes=[],
        shot_cuts=[], silences=[], preset_key="story_focused", spoiler_mode=spoiler_mode,
        essence_milestones=essence["milestones"])

    items: List[Dict[str, Any]] = []
    recap_parts: List[str] = []
    prev_end = 0.0
    for s in scenes:
        start = max(0.0, s["start"] + offset_sec)
        end = min(runtime_sec, s["end"] + offset_sec)
        if end - start < 3:
            continue
        if s.get("is_continuation"):
            recap = ""
        elif language == "Hindi":
            recap = s.get("narration_hi") or s.get("reason") or s.get("title", "")
        else:
            recap = s.get("narration_en") or s.get("reason") or s.get("title", "")
        if recap:
            recap_parts.append(recap)
        items.append({
            "act": s.get("act", ""),
            "title": s.get("title", ""),
            "start_sec": round(start, 1),
            "end_sec": round(end, 1),
            "jump_to": format_seconds(start),
            "watch_until": format_seconds(end),
            "watch_sec": round(end - start),
            "skip_before_sec": round(max(0.0, start - prev_end)),
            "recap": recap,
            "is_continuation": bool(s.get("is_continuation"))
        })
        prev_end = end

    timing = essence.get("timing", "percent")
    return {
        "title": essence.get("movie_title", title),
        "source": essence.get("source"),
        "timing": timing,
        "accuracy_note": ACCURACY_NOTES.get(timing, ACCURACY_NOTES["percent"]),
        "is_generic": essence.get("is_generic", False),
        "essence_theme": essence.get("essence_theme", ""),
        "runtime_sec": runtime_sec,
        "runtime_formatted": format_seconds(runtime_sec),
        "target_minutes": target_minutes,
        "offset_sec": offset_sec,
        "total_watch_sec": round(sum(i["watch_sec"] for i in items)),
        "total_watch_formatted": format_seconds(sum(i["watch_sec"] for i in items)),
        "language": language,
        "spoiler_mode": spoiler_mode,
        "items": items,
        "story_recap": " ".join(recap_parts),
        "legal_note": ("This guide only points to timestamps inside your own official player. It does not copy, "
                       "download or modify the stream.")
    }


def guide_to_text(guide: Dict[str, Any]) -> str:
    lines = [f"CineCut Watch Guide: {guide['title']}",
             f"Runtime {guide['runtime_formatted']} -> watch about {guide['total_watch_formatted']}", "",
             f"Accuracy: {guide['accuracy_note']}", ""]
    for n, it in enumerate(guide["items"], 1):
        skip = f" (skip {format_seconds(it['skip_before_sec'])})" if it["skip_before_sec"] > 20 else ""
        lines.append(f"{n:2d}. {it['jump_to']} -> {it['watch_until']}{skip}  {it['title']}")
        if it["recap"]:
            lines.append(f"    {it['recap']}")
    lines += ["", guide["legal_note"]]
    return "\n".join(lines)
