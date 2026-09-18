import os
import subprocess
from typing import List, Optional, Tuple
from backend.config import FFMPEG_BIN

VERTICAL_FIT = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"


def _even(n: float) -> int:
    n = int(n)
    return n - (n % 2)


def _cascade_path() -> Optional[str]:
    try:
        import cv2
    except ImportError:
        return None
    candidates = []
    data = getattr(cv2, "data", None)
    if data is not None and getattr(data, "haarcascades", None):
        candidates.append(os.path.join(data.haarcascades, "haarcascade_frontalface_default.xml"))
    base = os.path.dirname(cv2.__file__)
    candidates += [os.path.join(base, "data", "haarcascade_frontalface_default.xml")]
    return next((p for p in candidates if os.path.exists(p)), None)


def _sample_face_centers(video_path: str, start_sec: float, duration_sec: float, src_w: int, src_h: int,
                         samples: int) -> List[Tuple[float, float]]:
    """Returns [(t_relative, face_center_x_in_source_pixels)] for sampled frames that contain a face."""
    import cv2
    import numpy as np

    cascade_file = _cascade_path()
    if not cascade_file:
        return []
    detector = cv2.CascadeClassifier(cascade_file)
    sw = 480
    sh = max(2, _even(src_h * sw / src_w))
    fps = samples / max(1.0, duration_sec)
    cmd = [FFMPEG_BIN, "-ss", f"{start_sec:.2f}", "-t", f"{duration_sec:.2f}", "-i", str(video_path),
           "-vf", f"fps={fps:.5f},scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    frame_size = sw * sh
    raw = proc.stdout
    points = []
    for i in range(len(raw) // frame_size):
        frame = np.frombuffer(raw[i * frame_size:(i + 1) * frame_size], dtype=np.uint8).reshape((sh, sw))
        faces = detector.detectMultiScale(frame, scaleFactor=1.15, minNeighbors=4, minSize=(24, 24))
        if len(faces) > 0:
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            points.append(((i + 0.5) / fps, (fx + fw / 2.0) * src_w / sw))
    return points


def compute_dynamic_crop_offset(
    video_path: str,
    start_sec: float,
    duration_sec: float,
    source_width: Optional[int] = None,
    source_height: Optional[int] = None,
    samples: int = 8,
    fallback_filter: Optional[str] = None
) -> Tuple[int, str]:
    """
    Pan & scan for 9:16 shorts. Samples several frames across the clip, finds the largest face in each
    (OpenCV Haar cascade), smooths the path, and returns an FFmpeg crop filter whose x position glides
    between those points over time. Falls back to a centre crop when no face is found or OpenCV is missing.
    Returns (initial_x_offset, filter_string).
    """
    if not source_width or not source_height:
        try:
            from backend.video_engine.probe import get_video_metadata
            meta = get_video_metadata(video_path)
            source_width = meta.get("width") or 1920
            source_height = meta.get("height") or 1080
        except Exception:
            source_width, source_height = 1920, 1080

    crop_w = _even(source_height * 9 / 16)
    if crop_w >= source_width:
        return 0, VERTICAL_FIT  # already vertical or narrow: fit and pad instead of cropping
    max_x = source_width - crop_w
    default_x = max_x // 2

    try:
        points = _sample_face_centers(video_path, start_sec, duration_sec, source_width, source_height, samples)
    except Exception:
        points = []

    if fallback_filter and len(points) < max(2, samples // 2):
        return default_x, fallback_filter   # few faces (dance, wide shot, blackboard): show the whole frame instead
    if not points:
        return default_x, f"crop={crop_w}:{_even(source_height)}:{default_x}:0,scale=1080:1920"

    xs = [max(0.0, min(float(max_x), cx - crop_w / 2.0)) for _, cx in points]
    smoothed = []
    for i in range(len(xs)):
        window = xs[max(0, i - 1):i + 2]
        smoothed.append(sum(window) / len(window))
    keyframes = [(t, int(x)) for (t, _), x in zip(points, smoothed)]

    if len(keyframes) == 1:
        x0 = keyframes[0][1]
        return x0, f"crop={crop_w}:{_even(source_height)}:{x0}:0,scale=1080:1920"

    expr = str(keyframes[-1][1])
    for j in range(len(keyframes) - 2, -1, -1):
        t0, x0 = keyframes[j]
        t1, x1 = keyframes[j + 1]
        span = max(0.01, t1 - t0)
        expr = f"if(lt(t,{t1:.2f}),{x0}+({x1 - x0})*(t-{t0:.2f})/{span:.2f},{expr})"
    expr = f"if(lt(t,{keyframes[0][0]:.2f}),{keyframes[0][1]},{expr})"
    return keyframes[0][1], f"crop={crop_w}:{_even(source_height)}:'{expr}':0,scale=1080:1920"
