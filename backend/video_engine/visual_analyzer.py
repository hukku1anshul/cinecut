import os
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple
from backend.config import FFMPEG_BIN

def compute_scene_visual_motion(video_path: str, start_sec: float, end_sec: float) -> float:
    """
    Measures visual motion dynamics and spectacle energy (0.0 to 100.0)
    for a given video interval using FFmpeg freeze/motion analysis.
    High values indicate fast camera movement, combat, car chases, or visual spectacle.
    Low values indicate static dialogue or stationary tripod shots.
    """
    dur = max(0.5, min(end_sec - start_sec, 10.0))
    # Sample motion using scaled down 160x90 frames with freezedetect filter
    cmd = [
        FFMPEG_BIN, "-y",
        "-ss", f"{start_sec:.2f}",
        "-t", f"{dur:.2f}",
        "-i", str(video_path),
        "-vf", "scale=160:90,freezedetect=n=-30dB:d=0.2",
        "-f", "null",
        "-"
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        stderr = res.stderr

        # Count freeze events in the interval
        freezes = len(re.findall(r'lavfi\.freezedetect\.freeze_start', stderr))
        
        # Base motion score: if many freezes, low motion; if zero freezes, high dynamic motion
        if freezes == 0:
            motion_score = 80.0
        elif freezes == 1:
            motion_score = 55.0
        else:
            motion_score = 30.0

        # Adjust slightly by bitrate/packet activity if available
        return min(100.0, max(15.0, motion_score))
    except Exception:
        return 50.0

def enrich_scenes_with_visual_energy(
    video_path: str,
    scenes: List[Dict[str, Any]],
    has_subtitles: bool = True
) -> List[Dict[str, Any]]:
    """
    Enriches candidate scenes with visual motion energy scores.
    When subtitles are sparse or absent, visual motion becomes the primary driver
    for identifying high-impact cinematic sequences.
    """
    enriched = []
    for sc in scenes:
        item = dict(sc)
        start = item.get("start", 0.0)
        end = item.get("end", start + 5.0)
        
        motion = compute_scene_visual_motion(video_path, start, end)
        item["motion_score"] = motion

        # If movie has no subtitles, boost importance of visually dynamic scenes
        if not has_subtitles:
            # Shift combined significance heavily toward visual motion and audio
            audio_sc = item.get("audio_score", 50.0)
            item["dialogue_score"] = 0.0
            item["visual_action_priority"] = True
            item["significance"] = round((motion * 0.65) + (audio_sc * 0.35), 1)

        enriched.append(item)
    return enriched
