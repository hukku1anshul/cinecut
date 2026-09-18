import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from backend.config import FFMPEG_BIN


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp format: 00:01:23,456."""
    total_ms = int(round(max(0.0, seconds) * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    return f"{total_s // 3600:02d}:{(total_s // 60) % 60:02d}:{total_s % 60:02d},{ms:03d}"


def _active_sorted(scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active = [s for s in scenes if s.get("selected", True)]
    active.sort(key=lambda x: x["start"])
    return active


def generate_synced_srt(
    original_subtitles: List[Dict[str, Any]],
    selected_scenes: List[Dict[str, Any]],
    output_srt_path: str,
    durations: Optional[List[float]] = None
) -> str:
    """
    Re-times the original subtitles onto the condensed timeline.
    `durations` (actual rendered clip lengths) keeps the SRT in sync with the rendered file.
    """
    out_file = Path(output_srt_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    active = _active_sorted(selected_scenes)

    lines: List[str] = []
    offset = 0.0
    idx = 1
    for n, scene in enumerate(active):
        sc_start, sc_end = scene["start"], scene["end"]
        clip_len = durations[n] if durations and n < len(durations) else scene["duration"]
        for sub in original_subtitles:
            if sub["end"] > sc_start and sub["start"] < sc_end:
                rel_start = offset + max(0.0, sub["start"] - sc_start)
                rel_end = offset + min(clip_len, min(sc_end, sub["end"]) - sc_start)
                if rel_end - rel_start < 0.2:
                    continue
                lines += [str(idx), f"{format_srt_timestamp(rel_start)} --> {format_srt_timestamp(rel_end)}",
                          sub["text"], ""]
                idx += 1
        offset += clip_len

    out_file.write_text("\n".join(lines), encoding="utf-8")
    return str(out_file)


def _escape_ffmeta(value: str) -> str:
    for ch in ("\\", "=", ";", "#"):
        value = value.replace(ch, "\\" + ch)
    return value.replace("\n", " ")


def create_ffmetadata_chapters(
    selected_scenes: List[Dict[str, Any]],
    output_meta_path: str,
    durations: Optional[List[float]] = None,
    use_act_prefix: bool = True
) -> str:
    """
    Writes an FFMETADATA file with native MP4 chapter markers.
    `durations` should be the real rendered clip lengths so chapters line up with the video.
    """
    out_file = Path(output_meta_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    active = _active_sorted(selected_scenes)

    lines = [";FFMETADATA1"]
    accumulated_ms = 0
    for n, scene in enumerate(active):
        length = durations[n] if durations and n < len(durations) else scene["duration"]
        dur_ms = max(1, int(round(length * 1000)))
        title = scene.get("title", f"Scene {n + 1}")
        act = scene.get("act")
        if use_act_prefix and isinstance(act, str) and act:
            title = f"{act.split(':')[0]}: {title}"
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={accumulated_ms}",
                  f"END={accumulated_ms + dur_ms}", f"title={_escape_ffmeta(title)}"]
        accumulated_ms += dur_ms

    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out_file)


def embed_chapters_into_video(video_path: str, metadata_path: str, output_path: str) -> bool:
    """Embeds chapter metadata into the MP4 without re-encoding."""
    cmd = [FFMPEG_BIN, "-y", "-i", str(video_path), "-i", str(metadata_path),
           "-map", "0", "-map_metadata", "1", "-map_chapters", "1",
           "-codec", "copy", "-movflags", "+faststart", str(output_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=900)
        return res.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False
