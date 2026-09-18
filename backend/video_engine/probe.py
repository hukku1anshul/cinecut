import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from backend.config import FFMPEG_BIN, FFPROBE_BIN

_NVENC_AVAILABLE: Optional[bool] = None
_GPU_NAME: Optional[str] = None

# Subtitle codecs FFmpeg can convert to SRT (bitmap subs such as PGS/VobSub cannot)
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


def check_nvenc_support() -> bool:
    """Check if the NVIDIA NVENC hardware encoder works on this system (cached)."""
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is not None:
        return _NVENC_AVAILABLE
    cmd = [FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=10)
        _NVENC_AVAILABLE = res.returncode == 0
    except Exception:
        _NVENC_AVAILABLE = False
    return _NVENC_AVAILABLE


def get_gpu_name() -> Optional[str]:
    """Returns the NVIDIA GPU model name via nvidia-smi, or None."""
    global _GPU_NAME
    if _GPU_NAME is not None:
        return _GPU_NAME or None
    name = ""
    try:
        res = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            name = res.stdout.strip().splitlines()[0].strip()
    except Exception:
        name = ""
    _GPU_NAME = name
    return name or None


def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """Retrieve video, audio, and subtitle stream metadata via ffprobe."""
    path = Path(video_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")

    cmd = [FFPROBE_BIN, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise ValueError(f"Not a readable media file: {path.name} ({result.stderr.strip()[:200]})")
    data = json.loads(result.stdout or "{}")

    format_info = data.get("format", {})
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"
                         and not s.get("disposition", {}).get("attached_pic")), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for candidate in (format_info.get("duration"), (video_stream or {}).get("duration")):
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue

    sub_list = []
    for s in (s for s in streams if s.get("codec_type") == "subtitle"):
        tags = s.get("tags", {})
        codec = s.get("codec_name")
        sub_list.append({
            "index": s.get("index"),
            "codec": codec,
            "is_text": codec in TEXT_SUB_CODECS,
            "language": tags.get("language", "und"),
            "title": tags.get("title", tags.get("handler_name", "Subtitle"))
        })

    fps = 24.0
    if video_stream:
        try:
            num, den = map(float, video_stream.get("r_frame_rate", "24/1").split("/"))
            if den > 0 and num > 0:
                fps = round(num / den, 3)
        except Exception:
            fps = 24.0

    size = path.stat().st_size
    return {
        "file_path": str(path),
        "file_name": path.name,
        "file_size_bytes": size,
        "file_size_mb": round(size / (1024 * 1024), 2),
        "duration_sec": duration,
        "duration_formatted": format_seconds(duration),
        "width": int(video_stream.get("width", 0)) if video_stream else 0,
        "height": int(video_stream.get("height", 0)) if video_stream else 0,
        "video_codec": video_stream.get("codec_name", "unknown") if video_stream else None,
        "fps": fps,
        "audio_codec": audio_stream.get("codec_name", "unknown") if audio_stream else None,
        "audio_channels": int(audio_stream.get("channels", 0)) if audio_stream else 0,
        "has_audio": audio_stream is not None,
        "subtitle_streams": sub_list,
        "has_subtitles": any(s["is_text"] for s in sub_list),
        "nvenc_supported": check_nvenc_support()
    }


def format_seconds(seconds: float) -> str:
    """Format seconds as HH:MM:SS (or MM:SS under an hour)."""
    s = int(round(max(0.0, seconds or 0.0)))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h > 0 else f"{m:02d}:{sec:02d}"


def _first_text_subtitle_index(video_path: str) -> Optional[int]:
    try:
        meta = get_video_metadata(video_path)
    except Exception:
        return None
    preferred = [s for s in meta["subtitle_streams"] if s["is_text"]]
    for lang in ("eng", "en", "hin", "hi"):
        for s in preferred:
            if s["language"] == lang:
                return s["index"]
    return preferred[0]["index"] if preferred else None


def extract_subtitles_to_srt(video_path: str, output_srt_path: str, stream_index: Optional[int] = None) -> bool:
    """Extract an embedded text subtitle track to .srt (skips bitmap tracks like PGS)."""
    out_path = Path(output_srt_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if stream_index is None:
        stream_index = _first_text_subtitle_index(video_path)
        if stream_index is None:
            return False
    cmd = [FFMPEG_BIN, "-y", "-i", str(video_path), "-map", f"0:{stream_index}", "-c:s", "srt", str(out_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=120)
        return res.returncode == 0 and out_path.exists() and out_path.stat().st_size > 50
    except Exception:
        return False


def extract_thumbnail_frame(video_path: str, timestamp_sec: float, output_img_path: str, width: int = 360) -> bool:
    """Extract a single frame thumbnail at timestamp_sec."""
    out_path = Path(output_img_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG_BIN, "-y", "-ss", f"{max(0.0, timestamp_sec):.2f}", "-i", str(video_path),
           "-vframes", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=20)
        return res.returncode == 0 and out_path.exists()
    except Exception:
        return False


def get_media_duration(path: str) -> Optional[float]:
    """Duration in seconds of any audio/video file, or None if it cannot be read."""
    try:
        res = subprocess.run([FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=15)
        value = float(res.stdout.strip())
        return value if value > 0 else None
    except Exception:
        return None


def get_audio_duration(audio_path: str, default: float = 5.0) -> float:
    """Backwards-compatible duration helper (returns `default` when unreadable)."""
    value = get_media_duration(audio_path)
    return value if value is not None else default
