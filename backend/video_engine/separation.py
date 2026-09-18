"""
Vocal separation with Demucs (Meta's open htdemucs model), so a dub can replace the original voice completely.

The original soundtrack is split on the GPU into the voice and everything else (music, effects, ambience). It is
processed in 5-minute pieces that overlap by 2 seconds and are crossfaded, so memory stays small even for a full
film. The dub is then laid over "everything else". The model (~80 MB) downloads once to the torch cache.
"""
import importlib.util
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np

from backend.config import FFMPEG_BIN

SR = 44100
CHUNK_SEC = 300.0
OVERLAP_SEC = 2.0
_model = None
_lock = threading.Lock()


def available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def _load():
    global _model
    with _lock:
        if _model is None:
            import torch
            from demucs.pretrained import get_model
            m = get_model("htdemucs")
            m.eval()
            if torch.cuda.is_available():
                m.cuda()
            _model = m
    return _model


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip() or 0)
    except ValueError:
        return 0.0


def _read(path: str, start: float, seconds: float) -> np.ndarray:
    r = subprocess.run([FFMPEG_BIN, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", path, "-vn",
                        "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"], capture_output=True)
    data = np.frombuffer(r.stdout, dtype=np.float32)
    return data.reshape(-1, 2).T.copy() if data.size else np.zeros((2, 0), dtype=np.float32)


def separate(src_path: str, background_wav: str, progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, float]:
    """Writes everything except the voice to background_wav (44.1 kHz stereo). Returns timing and levels."""
    import torch
    from demucs.apply import apply_model
    progress = progress or (lambda p, m: None)
    t0 = time.time()
    model = _load()
    device = next(model.parameters()).device
    voice = model.sources.index("vocals")
    keep = [i for i in range(len(model.sources)) if i != voice]
    total = _duration(src_path)
    if total <= 0:
        raise RuntimeError("The video has no readable audio.")
    n_ov = int(OVERLAP_SEC * SR)
    tail: Optional[np.ndarray] = None
    removed_energy, kept_energy = 0.0, 0.0
    with wave.open(background_wav, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SR)
        pos = 0.0
        while pos < total - 0.05:
            last = pos + CHUNK_SEC >= total - 0.05
            wav = _read(src_path, pos, CHUNK_SEC + (0 if last else OVERLAP_SEC))
            if wav.shape[1] == 0:
                break
            ref = wav.mean(0)
            mean, std = float(ref.mean()), float(ref.std()) + 1e-8
            x = torch.from_numpy((wav - mean) / std)[None].to(device)
            with torch.no_grad():
                stems = apply_model(model, x, device=device, split=True, overlap=0.25, progress=False)[0]
            stems = stems * std + mean
            bg = stems[keep].sum(0).float().cpu().numpy()
            vo = stems[voice].float().cpu().numpy()
            removed_energy += float((vo ** 2).mean())
            kept_energy += float((bg ** 2).mean())
            if tail is not None:
                k = min(n_ov, bg.shape[1], tail.shape[1])
                ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
                bg[:, :k] = tail[:, :k] * (1 - ramp) + bg[:, :k] * ramp
            if last:
                body, tail = bg, None
            else:
                body, tail = bg[:, :-n_ov], bg[:, -n_ov:]
            out.writeframes((np.clip(body.T, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
            pos += CHUNK_SEC
            progress(min(99.0, 100.0 * pos / total), f"Removing the original voice: {min(pos, total) / 60:.1f} of {total / 60:.1f} min")
            del x, stems
            if device.type == "cuda":
                torch.cuda.empty_cache()
    return {"sec": round(time.time() - t0, 1), "minutes": round(total / 60, 2),
            "voice_share": round(removed_energy / max(1e-12, removed_energy + kept_energy), 3)}
