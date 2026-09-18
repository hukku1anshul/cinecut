"""
Copyright risk check.

This module used to promise a "Fair-Use Shield" that "bypasses Content ID fingerprinting". That was not
true: Content ID is designed to match mirrored/cropped footage, and none of these edits change the legal
analysis. What remains here is an honest checklist built from the fair-use factors the app can measure
(amount of the work used, length of continuous excerpts, share of original commentary). It is a heuristic,
not legal advice.
"""
import math
from typing import List, Dict, Any, Optional

DEFAULT_MAX_CLIP_SEC = 7.0

DISCLAIMER = ("Heuristic checklist only, not legal advice. A condensed recap of a whole film is high-risk "
              "for copyright claims and YouTube monetization even when this score is high.")


def enforce_fair_use_clipping(scenes: List[Dict[str, Any]], max_clip_sec: float = DEFAULT_MAX_CLIP_SEC) -> List[Dict[str, Any]]:
    """
    Optional editing style: splits scenes longer than max_clip_sec into short excerpts, leaving room for
    commentary between them. Only the first part keeps the narration so it is not repeated.
    """
    capped = []
    for scene in scenes:
        dur = scene.get("duration", scene.get("end", 0) - scene.get("start", 0))
        if dur <= max_clip_sec:
            capped.append(dict(scene))
            continue
        start_time, end_time = scene["start"], scene["end"]
        slice_duration = min(max_clip_sec, dur / math.ceil(dur / max_clip_sec))
        curr, part = start_time, 1
        while curr < end_time - 0.01:
            curr_end = min(curr + slice_duration, end_time)
            if curr_end - curr >= 1.5:
                new_scene = dict(scene)
                new_scene.update({"start": round(curr, 2), "end": round(curr_end, 2),
                                  "duration": round(curr_end - curr, 2),
                                  "title": f"{scene.get('title', 'Scene')} (Part {part})",
                                  "clip_capped": True, "original_duration": dur})
                if part > 1:
                    for key in ("bridge_narration", "voiceover_audio_path", "voiceover_url",
                                "voiceover_text", "narration_hi", "narration_en"):
                        new_scene[key] = None
                    new_scene["is_continuation"] = True
                capped.append(new_scene)
                part += 1
            curr = curr_end
    return capped


def calculate_fair_use_score(
    scenes: List[Dict[str, Any]],
    has_horizontal_flip: bool = False,
    max_clip_sec: float = DEFAULT_MAX_CLIP_SEC,
    source_duration_sec: Optional[float] = None
) -> Dict[str, Any]:
    """
    Transformation checklist (0-100, higher = more of the factors courts and YouTube look at):
      - Share of the source used (35 pts): less is better (fair-use factor 3, and market substitution, factor 4)
      - Continuous clip length (35 pts): short excerpts illustrating commentary
      - Original commentary coverage (30 pts): narration/commentary on the clips
    Mirroring earns no points; it does not change the legal analysis.
    """
    active = [s for s in scenes if s.get("selected", True)]
    if not active:
        return {"total_score": 0, "rating": "No active scenes", "badge_class": "shield-low", "breakdown": {},
                "recommendations": ["Select at least one scene."], "disclaimer": DISCLAIMER}

    durations = [s.get("duration", s["end"] - s["start"]) for s in active]
    longest = max(durations)
    used = sum(durations)
    over_limit = sum(1 for d in durations if d > max_clip_sec + 0.1)

    clip_pts = round(35.0 * (len(active) - over_limit) / len(active), 1)
    share = (used / source_duration_sec) if source_duration_sec else None
    if share is None:
        share_pts = 17.5
    elif share <= 0.10:
        share_pts = 35.0
    elif share <= 0.25:
        share_pts = round(35.0 - (share - 0.10) / 0.15 * 20.0, 1)
    elif share <= 0.50:
        share_pts = round(15.0 - (share - 0.25) / 0.25 * 15.0, 1)
    else:
        share_pts = 0.0

    with_vo = sum(1 for s in active if s.get("voiceover_audio_path") or s.get("bridge_narration"))
    vo_ratio = with_vo / len(active)
    vo_pts = round(min(30.0, vo_ratio / 0.6 * 30.0), 1)
    total = int(round(clip_pts + share_pts + vo_pts))

    recs = []
    if share is not None and share > 0.25:
        recs.append(f"This cut uses {share * 100:.0f}% of the film. Using a large share weighs against fair use "
                    "and can substitute for watching the film.")
    if over_limit:
        recs.append(f"{over_limit} clip(s) run longer than {max_clip_sec:g}s (longest {longest:.0f}s). Long continuous "
                    "clips are the most likely to be claimed by Content ID.")
    if vo_ratio < 0.6:
        recs.append("Add your own commentary or analysis over more scenes. Plot narration alone is treated as "
                    "reused content on YouTube.")
    if has_horizontal_flip:
        recs.append("Mirroring is only a style effect. It does not avoid Content ID and does not help a fair-use claim.")
    recs.append(DISCLAIMER)

    if total >= 75:
        rating, badge = "Lower risk profile", "shield-high"
    elif total >= 50:
        rating, badge = "Moderate risk profile", "shield-med"
    else:
        rating, badge = "High risk profile", "shield-low"

    return {
        "total_score": total,
        "rating": rating,
        "badge_class": badge,
        "over_limit_count": over_limit,
        "longest_clip_sec": round(longest, 1),
        "source_share_pct": round(share * 100, 1) if share is not None else None,
        "vo_coverage_pct": round(vo_ratio * 100, 1),
        "has_horizontal_flip": has_horizontal_flip,
        "breakdown": {
            "source_share_points": share_pts,
            "clip_length_points": clip_pts,
            "duration_points": clip_pts,
            "commentary_points": vo_pts,
            "visual_points": 0.0
        },
        "recommendations": recs,
        "disclaimer": DISCLAIMER
    }
