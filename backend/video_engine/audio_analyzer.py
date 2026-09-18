import re
import subprocess
from bisect import bisect_left
from typing import List, Dict, Any, Tuple, Optional
from backend.config import FFMPEG_BIN

SILENCE_FLOOR_DB = -90.0


def analyze_audio_track(video_path: str, noise_db: int = -30, min_silence: float = 0.4,
                        window_sec: float = 1.0, timeout: Optional[float] = None) -> Dict[str, Any]:
    """
    One FFmpeg pass over the audio track that returns both:
      - silences: [(start, end), ...] from silencedetect (dialogue pauses)
      - energy:   per-window RMS loudness in dB (index i covers i*window_sec .. (i+1)*window_sec)
    Audio is resampled to 8 kHz first, which keeps a 3-hour film to roughly half a minute.
    """
    sr = 8000
    samples = max(1, int(sr * window_sec))
    af = (f"aresample={sr},silencedetect=noise={noise_db}dB:d={min_silence},"
          f"asetnsamples=n={samples}:p=0,astats=metadata=1:reset=1,"
          f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    cmd = [FFMPEG_BIN, "-hide_banner", "-nostats", "-i", str(video_path),
           "-vn", "-sn", "-dn", "-af", af, "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as te:
        stdout = _to_text(te.stdout)
        stderr = _to_text(te.stderr)
    except Exception:
        return {"silences": [], "energy": [], "window_sec": window_sec}

    return {
        "silences": _parse_silences(stderr, min_silence),
        "energy": _parse_rms(stdout, window_sec),
        "window_sec": window_sec
    }


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_silences(stderr: str, min_silence: float) -> List[Tuple[float, float]]:
    intervals: List[Tuple[float, float]] = []
    pending: Optional[float] = None
    for m in re.finditer(r"silence_(start|end):\s*(-?[\d\.]+)", stderr):
        kind, value = m.group(1), max(0.0, float(m.group(2)))
        if kind == "start":
            pending = value
        elif pending is not None:
            intervals.append((round(pending, 3), round(value, 3)))
            pending = None
    if pending is not None:
        intervals.append((round(pending, 3), round(pending + min_silence, 3)))
    return intervals


def _parse_rms(stdout: str, window_sec: float) -> List[float]:
    energy: List[float] = []
    current_t = None
    for line in stdout.splitlines():
        if "pts_time:" in line:
            m = re.search(r"pts_time:([\d\.]+)", line)
            current_t = float(m.group(1)) if m else None
        elif "RMS_level=" in line:
            raw = line.split("=", 1)[1].strip()
            try:
                db = float(raw)
            except ValueError:
                db = SILENCE_FLOOR_DB
            if db != db or db < SILENCE_FLOOR_DB:  # NaN or -inf
                db = SILENCE_FLOOR_DB
            idx = int(round(current_t / window_sec)) if current_t is not None else len(energy)
            while len(energy) < idx:
                energy.append(SILENCE_FLOOR_DB)
            if idx < len(energy):
                energy[idx] = db
            else:
                energy.append(db)
    return [round(v, 1) for v in energy]


def detect_silence_intervals(video_path: str, noise_db: int = -30, min_duration: float = 0.4) -> List[Tuple[float, float]]:
    """Backwards-compatible: silence intervals only."""
    return analyze_audio_track(video_path, noise_db=noise_db, min_silence=min_duration)["silences"]


def snap_timestamp_to_silence(timestamp: float, silences: List[Tuple[float, float]],
                              is_end_boundary: bool = True, max_shift: float = 2.5) -> float:
    """
    Snaps a cut point into a dialogue pause so actors are not cut mid-word.
    End boundaries prefer the moment speech stops (silence start);
    start boundaries prefer the moment just before speech resumes (silence end).
    """
    if not silences:
        return timestamp
    best, best_cost = timestamp, float("inf")
    for s_start, s_end in silences:
        if s_start - max_shift > timestamp:
            break
        if s_end + max_shift < timestamp:
            continue
        if is_end_boundary:
            options = ((s_start + 0.1, 1.0), (s_end - 0.05, 1.6))
        else:
            options = ((s_end - 0.05, 1.0), (s_start + 0.1, 1.6))
        for point, weight in options:
            dist = abs(point - timestamp)
            if dist <= max_shift and dist * weight < best_cost:
                best, best_cost = point, dist * weight
    return round(max(0.0, best), 2)


class EnergyIndex:
    """Percentile lookups over the per-second loudness profile of the whole film."""

    def __init__(self, energy: List[float], window_sec: float = 1.0):
        self.energy = energy or []
        self.window_sec = window_sec
        audible = [v for v in self.energy if v > SILENCE_FLOOR_DB]
        self.sorted_values = sorted(audible) if audible else []

    def mean_db(self, start: float, end: float) -> Optional[float]:
        if not self.energy:
            return None
        a = max(0, int(start / self.window_sec))
        b = min(len(self.energy), max(a + 1, int(end / self.window_sec) + 1))
        chunk = [v for v in self.energy[a:b] if v > SILENCE_FLOOR_DB]
        if not chunk:
            return SILENCE_FLOOR_DB
        return sum(chunk) / len(chunk)

    def percentile(self, start: float, end: float) -> float:
        """0-100 loudness rank of a time window relative to the whole film (50 when unknown)."""
        mean = self.mean_db(start, end)
        if mean is None or not self.sorted_values:
            return 50.0
        return round(100.0 * bisect_left(self.sorted_values, mean) / len(self.sorted_values), 1)

    def loudest_window(self, start: float, end: float, length: float) -> float:
        """Start time of the loudest `length`-second window inside [start, end]."""
        if not self.energy or end - start <= length:
            return start
        step = max(1, int(round(length / self.window_sec)))
        a = max(0, int(start / self.window_sec))
        b = min(len(self.energy), int(end / self.window_sec))
        vals = [max(v, -60.0) for v in self.energy[a:b]]
        if len(vals) <= step:
            return start
        best_i, best_sum, running = a, float("-inf"), 0.0
        for i, v in enumerate(vals):
            running += v
            if i >= step:
                running -= vals[i - step]
            if i >= step - 1 and running > best_sum:
                best_sum, best_i = running, a + i - step + 1
        return best_i * self.window_sec


def compute_audio_energy_profile(video_path: str, duration_sec: float, sample_step_sec: float = 5.0) -> List[Dict[str, float]]:
    """Backwards-compatible: loudness percentile sampled every `sample_step_sec` seconds."""
    analysis = analyze_audio_track(video_path)
    index = EnergyIndex(analysis["energy"])
    profile = []
    t = 0.0
    while t < max(duration_sec, sample_step_sec):
        profile.append({"time": t, "energy": index.percentile(t, t + sample_step_sec)})
        t += sample_step_sec
    return profile
