import re
import subprocess
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from backend.config import FFMPEG_BIN, TEMP_DIR
from backend.video_engine.probe import extract_thumbnail_frame, get_media_duration


def _run_scene_filter(video_path: str, threshold: float, timeout: float, use_hwaccel: bool) -> str:
    cmd = [FFMPEG_BIN, "-hide_banner", "-nostats"]
    if use_hwaccel:
        cmd += ["-hwaccel", "auto"]
    cmd += ["-i", str(video_path), "-an", "-sn", "-dn",
            "-vf", f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return proc.stderr
    except subprocess.TimeoutExpired as te:
        err = te.stderr
        return err.decode("utf-8", errors="replace") if isinstance(err, bytes) else (err or "")
    except Exception:
        return ""


def detect_scene_cuts(video_path: str, threshold: float = 0.35, max_cuts: int = 20000,
                      timeout: Optional[float] = None, min_gap_sec: float = 0.5,
                      duration_hint: Optional[float] = None) -> List[float]:
    """
    Detects camera shot transitions across the WHOLE video (downscaled decode, GPU decode when available).
    Returns sorted cut timestamps in seconds, starting with 0.0.
    """
    if timeout is None:
        duration = duration_hint or get_media_duration(video_path) or 7200.0
        timeout = max(300.0, duration * 0.6)

    output = _run_scene_filter(video_path, threshold, timeout, use_hwaccel=True)
    if "pts_time:" not in output:
        output = _run_scene_filter(video_path, threshold, timeout, use_hwaccel=False)

    cuts = [0.0]
    for match in re.finditer(r"pts_time:([\d\.]+)", output):
        t = float(match.group(1))
        if t - cuts[-1] >= min_gap_sec:
            cuts.append(round(t, 3))
            if len(cuts) >= max_cuts:
                break
    return cuts


def snap_timestamp_to_shot(timestamp: float, shot_cuts: List[float], max_shift: float = 1.5) -> float:
    """Snaps a timestamp to the closest camera cut within max_shift seconds."""
    if not shot_cuts:
        return timestamp
    i = bisect_left(shot_cuts, timestamp)
    best, best_diff = timestamp, float("inf")
    for j in (i - 1, i):
        if 0 <= j < len(shot_cuts):
            diff = abs(shot_cuts[j] - timestamp)
            if diff <= max_shift and diff < best_diff:
                best, best_diff = shot_cuts[j], diff
    return round(best, 2)


def cut_density_score(shot_cuts: List[float], start: float, end: float, video_duration: float) -> float:
    """
    0-100 editing-pace score: 50 = the film's average cuts per minute, 100 = twice as fast.
    Fast cutting is a strong, cheap proxy for action, montage and song sequences.
    """
    if not shot_cuts or len(shot_cuts) < 3 or end <= start or video_duration <= 0:
        return 50.0
    film_rate = len(shot_cuts) / video_duration
    count = bisect_left(shot_cuts, end) - bisect_left(shot_cuts, start)
    window_rate = count / (end - start)
    if film_rate <= 0:
        return 50.0
    return round(max(0.0, min(100.0, 50.0 * window_rate / film_rate)), 1)


def thumbnail_name_for(start: float) -> str:
    return f"t_{int(round(start * 100)):09d}.jpg"


def generate_scene_thumbnails(video_path: str, scenes: List[Dict[str, Any]], job_id: str) -> List[Dict[str, Any]]:
    """Generates thumbnails, named by timestamp so recalculated cuts never show stale images."""
    thumb_dir = TEMP_DIR / job_id / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)

    def work(scene: Dict[str, Any]) -> None:
        name = thumbnail_name_for(scene["start"])
        thumb_file = thumb_dir / name
        ok = thumb_file.exists()
        if not ok:
            point = scene["start"] + min(scene.get("duration", 10.0) * 0.3, 5.0)
            ok = extract_thumbnail_frame(video_path, point, str(thumb_file), width=320)
        scene["thumbnail_url"] = f"/api/thumb/{job_id}/{name}" if ok else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, scenes))
    return scenes
