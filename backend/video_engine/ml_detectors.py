"""
Learned detectors on the laptop GPU (used when PyTorch is installed; otherwise CineCut keeps its FFmpeg rules):
  - Shots: TransNetV2 finds every cut and gradual transition (fades, dissolves) far more reliably than a fixed
    FFmpeg scene threshold. Frames are decoded at 25 fps and 48x27 pixels and processed in chunks.
  - Sounds: PANNs (CNN14, trained on Google's AudioSet) tags every 2 seconds of audio with music, singing, speech,
    laughter, applause, cheering, explosions, gunshots, screams and crying. These feed the Songs, Comedy, Action and
    Emotional filters.
Both models are released from the GPU after use so rendering and the local LLM keep their memory.
"""
import gc as _gc
import subprocess
from typing import Any, Dict, List, Optional

import numpy as np

from backend.config import FFMPEG_BIN

SHOT_FPS = 25
CHUNK_FRAMES = 15000
AUDIO_SR = 32000
WINDOW_SEC = 2.0
TAG_CLASSES = ["Speech", "Music", "Singing", "Laughter", "Applause", "Cheering", "Crowd", "Explosion", "Gunshot, gunfire",
               "Machine gun", "Screaming", "Crying, sobbing", "Smash, crash", "Slap, smack"]


def available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _free_gpu():
    try:
        import torch
        _gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _transnet_scores(model, frames):
    """TransNetV2's sliding windows (100 frames, step 50, middle 50 kept), 16 windows per GPU batch, half precision."""
    import torch
    n = frames.shape[0]
    pad_end = 25 + 50 - (n % 50 if n % 50 else 50)
    padded = torch.cat([frames[:1].expand(25, -1, -1, -1), frames, frames[-1:].expand(pad_end, -1, -1, -1)], 0)
    wins = padded.unfold(0, 100, 50).permute(0, 4, 1, 2, 3).contiguous()      # [windows, 100, 27, 48, 3]
    out = []
    use_half = frames.device.type == "cuda"
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=use_half):
        for b in range(0, wins.shape[0], 16):
            single, _ = model.predict_raw(wins[b:b + 16])
            out.append(single.reshape(single.shape[0], 100)[:, 25:75].float().cpu())
    return torch.cat(out).reshape(-1)[:n].numpy()


def detect_shots(video_path: str, duration: float, threshold: float = 0.5) -> Optional[List[float]]:
    """Cut timestamps (seconds, starting with 0.0) from TransNetV2, or None if it cannot run."""
    try:
        import torch
        from transnetv2_pytorch import TransNetV2
    except Exception:
        return None
    frame_bytes = 27 * 48 * 3
    cmd = [FFMPEG_BIN, "-v", "error", "-hwaccel", "auto", "-i", str(video_path), "-an",
           "-vf", f"fps={SHOT_FPS},scale=48:27:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:"]
    model = None
    try:
        model = TransNetV2(device="cuda" if torch.cuda.is_available() else "cpu")
        model.eval()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes * 2000)
        scores: List[np.ndarray] = []
        carry = np.zeros((0, 27, 48, 3), np.uint8)       # 50 frames of context carried between chunks
        while True:
            raw = proc.stdout.read(frame_bytes * CHUNK_FRAMES)
            if not raw:
                break
            frames = np.frombuffer(raw[: len(raw) // frame_bytes * frame_bytes], np.uint8).reshape(-1, 27, 48, 3)
            block = np.concatenate([carry, frames]) if len(carry) else frames
            single = _transnet_scores(model, torch.from_numpy(block.copy()).to(model.device))
            scores.append(single[len(carry):])
            carry = frames[-50:]
        proc.wait()
        if not scores:
            return None
        pred = np.concatenate(scores)
        cuts = [0.0]
        above = pred > threshold
        i = 0
        while i < len(above):
            if above[i]:
                j = i
                while j + 1 < len(above) and above[j + 1]:
                    j += 1
                t = ((i + j) / 2.0 + 1.0) / SHOT_FPS    # the new shot starts after the transition
                if t - cuts[-1] >= 0.4 and t < duration:
                    cuts.append(round(t, 3))
                i = j + 1
            else:
                i += 1
        return cuts
    except Exception as e:
        print(f"TransNetV2 shot detection failed, using FFmpeg instead: {e}")
        return None
    finally:
        del model
        _free_gpu()


def tag_audio(video_path: str, duration: float) -> Optional[Dict[str, Any]]:
    """{"window": 2.0, "classes": [...], "probs": [[...], ...]} for every 2 s of audio, or None."""
    try:
        import torch
        from panns_inference import AudioTagging, labels
    except Exception:
        return None
    idx = [labels.index(c) for c in TAG_CLASSES if c in labels]
    names = [labels[i] for i in idx]
    cmd = [FFMPEG_BIN, "-v", "error", "-i", str(video_path), "-vn", "-ac", "1", "-ar", str(AUDIO_SR), "-f", "f32le", "pipe:"]
    at = None
    try:
        at = AudioTagging(checkpoint_path=None, device="cuda" if torch.cuda.is_available() else "cpu")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        win = int(AUDIO_SR * WINDOW_SEC)
        batch_windows = 32
        rows: List[List[float]] = []
        while True:
            raw = proc.stdout.read(win * batch_windows * 4)
            if not raw:
                break
            audio = np.frombuffer(raw[: len(raw) // 4 * 4], np.float32)
            n = len(audio) // win
            if n == 0:
                break
            batch = audio[: n * win].reshape(n, win)
            clip, _ = at.inference(batch)
            rows += [[round(float(v), 3) for v in r[idx]] for r in clip]
        proc.wait()
        return {"window": WINDOW_SEC, "classes": names, "probs": rows} if rows else None
    except Exception as e:
        print(f"Audio tagging failed: {e}")
        return None
    finally:
        del at
        _free_gpu()


def _col(tags: Dict[str, Any], name: str) -> np.ndarray:
    try:
        return np.array([r[tags["classes"].index(name)] for r in tags["probs"]], dtype=np.float32)
    except (ValueError, KeyError):
        return np.zeros(len(tags.get("probs") or []), dtype=np.float32)


FLAVOR_LABELS = {"song": "Song sequence", "action": "Action sequence", "comedy": "Comedy scene", "emotional": "Emotional scene"}
MIN_LEN = {"song": 60.0, "action": 16.0, "comedy": 10.0, "emotional": 20.0}


def audio_segments(tags: Optional[Dict[str, Any]], flavor: str) -> List[Dict[str, Any]]:
    """Stretches where the audio tagger hears songs, laughter, action sounds or crying, in the detector format."""
    if not tags or not tags.get("probs") or flavor not in MIN_LEN:
        return []
    speech, music, singing = _col(tags, "Speech"), _col(tags, "Music"), _col(tags, "Singing")
    if flavor == "song":
        flags = ((music > 0.45) & (singing > 0.12)) | ((music > 0.6) & (speech < 0.25))
        strength = music + singing
    elif flavor == "comedy":
        strength = _col(tags, "Laughter")
        flags = strength > 0.2
    elif flavor == "action":
        strength = np.maximum.reduce([_col(tags, c) for c in ("Explosion", "Gunshot, gunfire", "Machine gun", "Screaming",
                                                                "Smash, crash", "Slap, smack")])
        flags = strength > 0.15
    else:
        strength = _col(tags, "Crying, sobbing")
        flags = strength > 0.12
    w = float(tags.get("window", WINDOW_SEC))
    segs, i, n = [], 0, len(flags)
    allow_gap = 3 if flavor == "song" else 2
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j, gap = i, 0
        k = i + 1
        while k < n and gap <= allow_gap:
            if flags[k]:
                j, gap = k, 0
            else:
                gap += 1
            k += 1
        start, end = i * w, (j + 1) * w
        if end - start >= MIN_LEN[flavor]:
            score = min(100.0, 45.0 + 60.0 * float(strength[i:j + 1].mean()) + (end - start) / 8.0)
            segs.append({"start": start, "end": end, "score": round(score, 1), "flavor": flavor,
                         "label": FLAVOR_LABELS[flavor], "evidence": "heard in the audio by the sound tagger"})
        i = j + 1
    segs.sort(key=lambda s: -s["score"])
    return segs
