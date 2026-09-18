"""
Music beds for the YouTube videos, made here so nothing needs a licence: real documentaries always have a quiet bed under
the voice, and a silent one sounds synthetic.

  tanpura(seconds)   the drone of Indian classical music: four strings (Pa, Sa, Sa, low Sa) plucked in turn, each note rich
                     in harmonics with the tanpura's "jawari" shimmer (upper partials swelling after the pluck), with a soft
                     hall reverb. For the Vedas and the hymn recitals.
  pad(seconds)       slow, warm held chords that change every few bars, for the classics and lectures.

Each bed is a seamless loop made once (loop()); the mix repeats it, sets its level about 21 dB under the voice and
ducks it further while someone speaks (see youtube._render).
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100


def _write(path: Path, x: np.ndarray) -> Path:
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.5
    stereo = np.stack([x, np.roll(x, int(SR * 0.013))], axis=1)          # a little width
    data = (stereo * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return path


def _reverb(x: np.ndarray, seconds: float = 2.6, mix: float = 0.35) -> np.ndarray:
    """A soft hall: a decaying cloud of noise convolved with the signal (by FFT)."""
    rng = np.random.default_rng(7)
    n = int(SR * seconds)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    ir[: int(SR * 0.02)] *= np.linspace(0, 1, int(SR * 0.02))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    wet /= np.max(np.abs(wet)) + 1e-9
    dry = x / (np.max(np.abs(x)) + 1e-9)
    return (1 - mix) * dry + mix * wet


def _pluck(freq: float, seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(SR * seconds)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, 26):                              # harmonics, slightly stretched like a real string
        f = freq * k * (1 + 0.0004 * k * k)
        if f > SR / 2.2:
            break
        base = 1.0 / k
        # jawari: upper partials swell a moment after the pluck and shimmer as they fade
        swell = 1 + (k / 10) * np.clip(t / 0.35, 0, 1) * np.exp(-t / (1.2 + 0.08 * k))
        shimmer = 1 + 0.25 * np.sin(2 * np.pi * (0.7 + 0.05 * k) * t + rng.uniform(0, 6.3))
        decay = np.exp(-t * (0.55 + 0.035 * k))
        out += base * swell * shimmer * decay * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.3))
    attack = np.clip(t / 0.012, 0, 1)
    return out * attack


def tanpura(seconds: float, path: Path, sa: float = 138.59) -> Path:
    """Pa, Sa, Sa, low Sa, round and round (about 1.25 s apart), each ringing for several seconds."""
    rng = np.random.default_rng(int(seconds * 10))
    total = int(SR * (seconds + 6))
    x = np.zeros(total)
    strings = (sa * 0.75, sa, sa, sa / 2)                # Pa below Sa, two Sa, low Sa
    t, i = 0.0, 0
    while t < seconds + 1:
        f = strings[i % 4] * (1 + rng.normal(0, 0.0008))
        note = _pluck(f, 6.0, rng) * (0.9 if i % 4 else 1.0)
        a = int(t * SR)
        x[a:a + len(note)] += note[: max(0, total - a)]
        t += 1.25 + rng.normal(0, 0.02)
        i += 1
    x = _reverb(x[: int(SR * seconds)])
    fade = int(SR * 3)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    return _write(path, x)


def pad(seconds: float, path: Path) -> Path:
    """Warm held chords (I, vi, IV, V in a low register), a new chord every eight seconds, crossfaded."""
    chords = [(0, 4, 7), (-3, 0, 4), (-7, -3, 0), (-5, -1, 2)]
    root = 110.0
    n = int(SR * seconds)
    t = np.arange(n) / SR
    x = np.zeros(n)
    bar = 8.0
    for c in range(int(seconds // bar) + 2):
        start = c * bar - 2
        env = np.clip(1 - np.abs((t - (start + bar / 2 + 1)) / (bar / 2 + 1.5)), 0, 1) ** 1.5
        if not env.any():
            continue
        for semis in chords[c % 4]:
            f = root * 2 ** (semis / 12)
            for det in (-0.12, 0.0, 0.12):              # three slightly detuned voices per note: a soft chorus
                x += env * (np.sin(2 * np.pi * (f + det) * t) + 0.3 * np.sin(2 * np.pi * 2 * (f + det) * t)) / 3
    x = _reverb(x, 3.2, 0.45)
    fade = int(SR * 3)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    return _write(path, x)


LOOP_SECONDS = 160.0            # a multiple of the pad's 32-second chord cycle
LOOPS = Path(__file__).resolve().parents[2] / "output" / "youtube" / "_music"


def _seamless(x: np.ndarray, length: int) -> np.ndarray:
    """A loop that joins without a seam: the start is crossfaded with what came after the end."""
    over = len(x) - length
    y = x[:length].copy()
    ramp = np.linspace(0, 1, over)
    y[:over] = x[:over] * ramp + x[length:] * (1 - ramp)
    return y


def loop(kind: str) -> Path:
    """A 160-second seamless loop of the tanpura or the pad, made once and kept (the mix repeats it for the video's
    length and fades it in and out)."""
    LOOPS.mkdir(parents=True, exist_ok=True)
    path = LOOPS / f"{kind}.wav"
    if path.exists():
        return path
    n, over = int(SR * LOOP_SECONDS), int(SR * 6)
    if kind == "tanpura":
        rng = np.random.default_rng(160)
        total = n + over + int(SR * 6)
        x = np.zeros(total)
        sa = 138.59
        strings = (sa * 0.75, sa, sa, sa / 2)
        t, i = 0.0, 0
        while t < (n + over) / SR:
            f = strings[i % 4] * (1 + rng.normal(0, 0.0008))
            note = _pluck(f, 6.0, rng) * (0.9 if i % 4 else 1.0)
            a = int(t * SR)
            x[a:a + len(note)] += note[: max(0, total - a)]
            t += 1.25 + rng.normal(0, 0.02)
            i += 1
        x = _reverb(x[: n + over])
    else:
        chords = [(0, 4, 7), (-3, 0, 4), (-7, -3, 0), (-5, -1, 2)]
        t = np.arange(n + over) / SR
        x = np.zeros(n + over)
        bar = 8.0
        for c in range(int((n + over) / SR // bar) + 2):
            start = c * bar - 2
            env = np.clip(1 - np.abs((t - (start + bar / 2 + 1)) / (bar / 2 + 1.5)), 0, 1) ** 1.5
            if not env.any():
                continue
            for semis in chords[c % 4]:
                f = 110.0 * 2 ** (semis / 12)
                for det in (-0.12, 0.0, 0.12):
                    x += env * (np.sin(2 * np.pi * (f + det) * t) + 0.3 * np.sin(2 * np.pi * 2 * (f + det) * t)) / 3
        x = _reverb(x, 3.2, 0.45)
    return _write(path, _seamless(x, n))


def bed(series: str) -> Path:
    """The loop for a series: the tanpura under the Vedas and the hymn recitals, the pad under everything else."""
    return loop("tanpura" if series in ("vedas", "audio") else "pad")
