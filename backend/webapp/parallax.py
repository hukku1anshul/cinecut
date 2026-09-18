"""
Scenes that move like a film shot: a still picture becomes a 2.5D shot with a moving camera. A depth map (Depth Anything
V2 Small, on the GPU) tells near from far, so while the camera pushes in, drifts sideways or rises, near things move more
than far things, the way they do through a real lens. Frames are made on the GPU and piped straight to FFmpeg (NVENC when
it is there), and a whole sequence of shots is rendered in one pass with a dissolve between shots.
"""
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from backend.config import FFMPEG_BIN

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_depth = None

# camera moves: zoom from-to, pan (fraction of the frame) from-to, depth strength. "push" is a dolly in; "left"/"right" a
# sideways drift (truck); "rise" a pedestal up; "pull" a slow dolly out.
MOVES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "push":  {"zoom": (1.00, 1.12), "x": (0.0, 0.0), "y": (0.0, 0.0), "radial": (0.0, 0.07), "lateral": (0.0, 0.0), "vertical": (0.0, 0.0)},
    "still": {"zoom": (1.0, 1.0), "x": (0.0, 0.0), "y": (0.0, 0.0), "radial": (0.0, 0.0), "lateral": (0.0, 0.0), "vertical": (0.0, 0.0)},
    "pull":  {"zoom": (1.12, 1.01), "x": (0.0, 0.0), "y": (0.0, 0.0), "radial": (0.06, 0.0), "lateral": (0.0, 0.0), "vertical": (0.0, 0.0)},
    "left":  {"zoom": (1.08, 1.10), "x": (0.025, -0.025), "y": (0.0, 0.0), "radial": (0.0, 0.0), "lateral": (-0.035, 0.035), "vertical": (0.0, 0.0)},
    "right": {"zoom": (1.08, 1.10), "x": (-0.025, 0.025), "y": (0.0, 0.0), "radial": (0.0, 0.0), "lateral": (0.035, -0.035), "vertical": (0.0, 0.0)},
    "rise":  {"zoom": (1.09, 1.11), "x": (0.0, 0.0), "y": (0.025, -0.02), "radial": (0.0, 0.02), "lateral": (0.0, 0.0), "vertical": (-0.03, 0.03)},
}
ORDER = ("push", "left", "rise", "right", "pull")          # a varied rhythm when a shot does not ask for a move
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}             # a real clip (e.g. Firefly Image to Video) instead of a still


def depth_model():
    global _depth
    if _depth is None:
        from transformers import pipeline
        _depth = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf", device=0 if DEVICE == "cuda" else -1)
    return _depth


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize to cover w x h and crop the middle (a little above the middle, where faces usually are)."""
    s = max(w / img.width, h / img.height)
    im = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    left = (im.width - w) // 2
    top = int((im.height - h) * 0.42)
    return im.crop((left, top, left + w, top + h))


def _trim_bars(img: Image.Image) -> Image.Image:
    """Cut away black letterbox or pillarbox bars (some generated pictures come with them), so they never show during a move."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    dark_rows = (g.mean(axis=1) < 12) & (g.std(axis=1) < 8)
    dark_cols = (g.mean(axis=0) < 12) & (g.std(axis=0) < 8)

    def edge(mask: np.ndarray) -> Tuple[int, int]:
        a, b = 0, len(mask)
        while a < b and mask[a]:
            a += 1
        while b > a and mask[b - 1]:
            b -= 1
        return a, b

    top, bottom = edge(dark_rows)
    left, right = edge(dark_cols)
    if (bottom - top) < img.height * 0.5 or (right - left) < img.width * 0.5:
        return img                                               # mostly dark: a night scene, not bars
    return img.crop((left, top, right, bottom)) if (top, left, bottom, right) != (0, 0, img.height, img.width) else img


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


class Shot:
    """One picture ready for camera moves: the picture and its depth on the GPU at a working size with room to move."""

    def __init__(self, image_path: Path, size: Tuple[int, int], move: str):
        W, H = size
        self.W, self.H = W, H
        self.move = MOVES.get(move, MOVES["push"])
        still = move == "still"                                              # the board: shown as it is, no depth
        ww, wh = (W, H) if still else (int(W * 1.18) // 2 * 2, int(H * 1.18) // 2 * 2)
        img = _cover(_trim_bars(Image.open(image_path).convert("RGB")), ww, wh)
        d = (np.zeros((wh, ww), np.float32) if still else
             np.asarray(depth_model()(img)["depth"].resize((ww, wh), Image.BILINEAR), dtype=np.float32))
        d = (d - d.min()) / max(1e-6, float(d.max() - d.min()))              # 1 = near
        src = torch.from_numpy(np.asarray(img, dtype=np.float32) / 255.0).permute(2, 0, 1)[None].to(DEVICE)
        dep = torch.from_numpy(d)[None, None].to(DEVICE)
        k = 21                                                               # soften depth edges, so nothing tears
        dep = F.avg_pool2d(F.pad(dep, (k // 2,) * 4, mode="replicate"), k, stride=1)
        dep = F.avg_pool2d(F.pad(dep, (k // 2,) * 4, mode="replicate"), k, stride=1)
        self.src, self.dep = src, dep - 0.5                                  # depth centred: far < 0 < near
        ys, xs = torch.meshgrid(torch.linspace(-1, 1, H, device=DEVICE), torch.linspace(-1, 1, W, device=DEVICE), indexing="ij")
        # the output frame covers W/ww of the working picture's width (normalised coordinates of the working picture)
        self.bx, self.by = xs * (W / ww), ys * (H / wh)

    def frame(self, t: float) -> torch.Tensor:
        """The shot at time t (0..1) as a 3 x H x W tensor in 0..1."""
        m, e = self.move, _ease(max(0.0, min(1.0, t)))
        lerp = lambda a: a[0] + (a[1] - a[0]) * e
        z, px, py = lerp(m["zoom"]), lerp(m["x"]), lerp(m["y"])
        gx, gy = self.bx / z + px, self.by / z + py
        # depth where each output pixel lands (one step of refinement is enough for small moves)
        d = F.grid_sample(self.dep, torch.stack([gx, gy], -1)[None], mode="bilinear", padding_mode="border", align_corners=True)[0, 0]
        r, lx, vy = lerp(m["radial"]), lerp(m["lateral"]), lerp(m["vertical"])
        gx = gx / (1 + r * d) - lx * d
        gy = gy / (1 + r * d) - vy * d
        out = F.grid_sample(self.src, torch.stack([gx, gy], -1)[None], mode="bilinear", padding_mode="border", align_corners=True)
        return out[0]


class VideoShot:
    """A real clip as a shot: decoded by FFmpeg as its frames are needed, covering the frame, slowed by up to 2.5x to fill
    the shot (at most 1.5x), then holding its last frame."""

    def __init__(self, path: Path, size: Tuple[int, int], seconds: float, fps: int):
        W, H = size
        self.W, self.H = W, H
        try:
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                       capture_output=True, text=True).stdout.strip() or 0)
        except (ValueError, OSError):
            dur = 0.0
        k = min(1.5, max(1.0, seconds / dur)) if dur > 0.1 else 1.0         # never slow motion
        self.n = max(1, int(math.ceil(seconds * fps)))
        self.proc = subprocess.Popen([FFMPEG_BIN, "-v", "error", "-i", str(path), "-an", "-vf",
                                      f"setpts={k:.4f}*PTS,fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.got, self.last = 0, None

    def frame(self, t: float) -> torch.Tensor:
        want = min(self.n - 1, max(0, int(round(max(0.0, min(1.0, t)) * (self.n - 1)))))
        size = self.W * self.H * 3
        while self.got <= want:
            buf = self.proc.stdout.read(size)
            if len(buf) < size:                          # the clip has ended: hold its last frame
                break
            self.last, self.got = buf, self.got + 1
        if self.last is None:
            return torch.zeros(3, self.H, self.W, device=DEVICE)
        arr = np.frombuffer(self.last, np.uint8).reshape(self.H, self.W, 3)
        return torch.from_numpy(arr.copy()).to(DEVICE).permute(2, 0, 1).float() / 255.0

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


def _encoder(out: Path, size: Tuple[int, int], fps: int) -> subprocess.Popen:
    W, H = size
    nvenc = subprocess.run([FFMPEG_BIN, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout.find("h264_nvenc") >= 0
    codec = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-b:v", "0"] if nvenc else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    return subprocess.Popen([FFMPEG_BIN, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
                             "-i", "-", *codec, "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)


def render_sequence(shots: Sequence[Tuple[Path, float, float, Optional[str]]], total: float, out: Path,
                    size: Tuple[int, int] = (1920, 1080), fps: int = 24, fade: float = 0.8, max_shot: float = 30.0) -> Path:
    """shots: (picture, start s, end s, move or None) in time order, covering 0..total. One pass: each frame is the active
    shot at its point in the move; around a cut the two shots dissolve over `fade` seconds. Two shots are held on the GPU
    at a time. A picture held longer than `max_shot` is split into several moves (push, then a drift, ...), so the camera
    never lingers on one move for long; the board ("still") is never split."""
    split: List[Tuple[Path, float, float, Optional[str]]] = []
    for i, (p, a, b, mv) in enumerate(sorted(shots, key=lambda s: s[1])):
        n = 1 if mv == "still" or Path(p).suffix.lower() in VIDEO_EXT else max(1, math.ceil((b - a) / max_shot))
        base = ORDER.index(mv) if mv in ORDER else i % len(ORDER)
        for j in range(n):
            split.append((p, a + (b - a) * j / n, a + (b - a) * (j + 1) / n, mv if j == 0 and mv else ORDER[(base + j) % len(ORDER)]))
    shots = split
    n = int(math.ceil(total * fps))
    enc = _encoder(out, size, fps)
    cache: Dict[int, Any] = {}

    def shot(i: int):
        if i not in cache:
            for k in [k for k in cache if k < i - 1]:
                if hasattr(cache[k], "close"):
                    cache[k].close()
                del cache[k]
            p, a, b, mv = shots[i]
            if Path(p).suffix.lower() in VIDEO_EXT:
                cache[i] = VideoShot(Path(p), size, max(0.5, b - a + fade), fps)
            else:
                cache[i] = Shot(Path(p), size, mv or ORDER[i % len(ORDER)])
        return cache[i]

    i = 0
    try:
        for f in range(n):
            t = f / fps
            while i + 1 < len(shots) and t >= shots[i + 1][1]:
                i += 1
            p, a, b, _ = shots[i]
            # a shot's move runs from the start of the dissolve into it (a - fade) to its end (b), so the move never jumps
            span = max(0.5, b - a + fade)
            img = shot(i).frame((t - (a - fade)) / span)
            if i + 1 < len(shots) and t > shots[i + 1][1] - fade:      # dissolve into the next shot
                p2, a2, b2, _ = shots[i + 1]
                w = _ease((t - (a2 - fade)) / fade)
                img = img * (1 - w) + shot(i + 1).frame((t - (a2 - fade)) / max(0.5, b2 - a2 + fade)) * w
            buf = (img.clamp(0, 1) * 255).to(torch.uint8).permute(1, 2, 0).contiguous().cpu().numpy()
            enc.stdin.write(buf.tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
        for c in cache.values():
            if hasattr(c, "close"):
                c.close()
        cache.clear()
        if DEVICE == "cuda":
            torch.cuda.empty_cache()
    if enc.returncode != 0:
        raise RuntimeError("the scene encoder failed")
    return out
