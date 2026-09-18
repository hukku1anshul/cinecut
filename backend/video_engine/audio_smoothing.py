import os
import subprocess
from pathlib import Path
from typing import Tuple
from backend.config import FFMPEG_BIN


def build_transition_filters(
    duration_sec: float,
    is_first_scene: bool = False,
    is_last_scene: bool = False,
    fade_audio_sec: float = 0.35,
    fade_video_sec: float = 0.25,
    curve: str = "esin"
) -> Tuple[str, str]:
    """
    Returns (video_filter, audio_filter) strings for cinematic scene transitions:
      - Audio: S-curve fade in/out so cuts never click or slam into a music cue.
      - Video: 0.25 s micro-dissolves; 0.5 s fade from black on the first scene, 0.8 s to black on the last.
    The renderer applies these in the same pass that cuts the clip, so every clip is encoded once.
    """
    duration_sec = max(0.3, float(duration_sec))
    cap = duration_sec / 3.0
    afade = min(fade_audio_sec, cap)
    af = (f"afade=t=in:st=0:d={afade:.2f}:curve={curve},"
          f"afade=t=out:st={max(0.0, duration_sec - afade):.2f}:d={afade:.2f}:curve={curve}")
    vin = min(0.50 if is_first_scene else fade_video_sec, cap)
    vout = min(0.80 if is_last_scene else fade_video_sec, cap)
    vf = f"fade=t=in:st=0:d={vin:.2f},fade=t=out:st={max(0.0, duration_sec - vout):.2f}:d={vout:.2f}"
    return vf, af


def apply_exponential_audio_crossfade(
    input_video_path: str,
    output_video_path: str,
    duration_sec: float,
    fade_sec: float = 0.35,
    curve: str = "esin"
) -> bool:
    """Applies S-curve audio fade-in/out to an existing segment (video stream copied)."""
    input_path = Path(input_video_path).resolve()
    if not input_path.exists():
        return False
    _, af_filter = build_transition_filters(duration_sec, fade_audio_sec=fade_sec, curve=curve)
    cmd = [FFMPEG_BIN, "-y", "-i", str(input_path), "-c:v", "copy", "-af", af_filter,
           "-c:a", "aac", "-b:a", "192k", str(output_video_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        return res.returncode == 0 and os.path.exists(output_video_path)
    except Exception:
        return False


def apply_cinematic_transition_smoothing(
    input_video_path: str,
    output_video_path: str,
    duration_sec: float,
    fade_audio_sec: float = 0.35,
    fade_video_sec: float = 0.25,
    is_first_scene: bool = False,
    is_last_scene: bool = False,
    curve: str = "esin"
) -> bool:
    """
    Standalone version of the transition smoothing for an already-cut segment.
    (The main renderer no longer calls this; it applies the same filters while cutting.)
    """
    input_path = Path(input_video_path).resolve()
    if not input_path.exists():
        return False
    vf_filter, af_filter = build_transition_filters(duration_sec, is_first_scene, is_last_scene,
                                                    fade_audio_sec, fade_video_sec, curve)
    from backend.video_engine.probe import check_nvenc_support
    vcodec = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21"] if check_nvenc_support()
              else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"])
    cmd = [FFMPEG_BIN, "-y", "-i", str(input_path), "-vf", vf_filter + ",format=yuv420p", *vcodec,
           "-af", af_filter, "-c:a", "aac", "-b:a", "192k", "-avoid_negative_ts", "make_zero",
           str(output_video_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and os.path.exists(output_video_path):
            return True
    except Exception:
        pass
    return apply_exponential_audio_crossfade(input_video_path, output_video_path, duration_sec, fade_audio_sec, curve)


def generate_ambient_transition_tone(output_wav_path: str, duration_sec: float = 0.5) -> bool:
    """Synthesizes a subtle low-frequency ambient bed used to bridge abrupt scene cuts."""
    cmd = [FFMPEG_BIN, "-y", "-f", "lavfi", "-i", f"sine=frequency=80:duration={duration_sec}",
           "-af", "afade=t=in:st=0:d=0.1:curve=esin,afade=t=out:st=0.2:d=0.3:curve=esout,volume=0.25",
           str(output_wav_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return res.returncode == 0 and os.path.exists(output_wav_path)
    except Exception:
        return False
