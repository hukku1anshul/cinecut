from typing import Dict, Any, Optional
from backend.video_engine.gemini_client import generate_json
from backend.video_engine.subtitle_parser import parse_srt_time

SYSTEM_PROMPT = """You are an elite film editor and story analyst.
Given a full-length movie's timestamped dialogue script, curate a cohesive condensed cut.

RULES:
1. NARRATIVE CONTINUITY: a first-time viewer must be able to follow the story: setup and inciting incident,
   rising conflict and midpoint, climax and resolution.
2. CHRONOLOGICAL ORDER: scenes strictly in order.
3. COHESIVE SCENES: each scene 25-120 seconds with a natural start and end; no 5-second fragments.
4. TIME BUDGET: the sum of (end_time - start_time) must be close to the requested total.
5. Output only the JSON object requested.
"""


def generate_ai_narrative_cut(
    transcript_text: str,
    target_minutes: int,
    total_movie_duration_sec: float,
    api_key: str,
    model_name: Optional[str] = None,
    style_preference: str = "Story & Plot Focus"
) -> Dict[str, Any]:
    """Asks Gemini to choose the scenes for the condensed cut from the transcript."""
    if not api_key:
        raise ValueError("Gemini API key is required for AI Director mode.")

    prompt = f"""Movie total duration: {int(total_movie_duration_sec // 60)} minutes.
Target condensed cut: {target_minutes} minutes ({target_minutes * 60} seconds total).
Editorial style: {style_preference}.

Timestamped dialogue transcript:
---
{transcript_text}
---

Return JSON:
{{
  "title": "Movie Summary Cut",
  "narrative_overview": "2-sentence summary of the story arc preserved in this cut",
  "scenes": [
    {{"act": "Act 1: Inciting Incident", "start_time": "00:03:15", "end_time": "00:05:45",
      "scene_title": "The Discovery", "narrative_reason": "Why this scene is essential", "importance_score": 95}}
  ]
}}"""
    result = generate_json(api_key, prompt, system=SYSTEM_PROMPT, temperature=0.2, timeout=180, model=model_name)

    scenes, total = [], 0.0
    for item in result.get("scenes", []):
        start_sec = parse_srt_time(str(item.get("start_time", "00:00:00")))
        end_sec = min(parse_srt_time(str(item.get("end_time", "00:00:00"))), total_movie_duration_sec)
        if end_sec - start_sec < 3.0 or start_sec >= total_movie_duration_sec:
            continue
        try:
            importance = int(item.get("importance_score", 85))
        except (TypeError, ValueError):
            importance = 85
        scenes.append({
            "act": item.get("act", "General Scene"),
            "start": round(start_sec, 2),
            "end": round(end_sec, 2),
            "duration": round(end_sec - start_sec, 2),
            "title": item.get("scene_title", "Key Scene"),
            "reason": item.get("narrative_reason", "Important narrative beat"),
            "importance": importance,
            "source": "gemini_ai",
            "selected": True
        })
        total += end_sec - start_sec
    scenes.sort(key=lambda s: s["start"])
    if not scenes:
        raise RuntimeError("Gemini did not return any usable scenes.")
    return {
        "title": result.get("title", "Movie Condensed Cut"),
        "overview": result.get("narrative_overview", ""),
        "total_selected_sec": round(total, 2),
        "target_minutes": target_minutes,
        "scenes": scenes
    }
