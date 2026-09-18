"""
Dubbing a creator's own video into Hindi or another Indian language.

1. Lines: the transcript is merged into spoken sentences, keeping their times.
2. Translate: lines go to the AI in batches of 40, each with the seconds it may last, so translations fit the time.
3. Voice: every line is voiced (Sarvam for Indian languages when SARVAM_API_KEY is set, Edge otherwise).
4. Fit: a line longer than its slot is sped up, at most 1.3 times, and may run into the pause after it. It never
   starts before the original line.
5. Remove the original voice: Demucs splits the soundtrack into the voice and everything else on the GPU; the dub is
   laid over music and effects only, which dip slightly under the new voice. If Demucs is not installed or fails, the
   original soundtrack is lowered to about 12% under each dubbed line instead (voice-over dubbing).
6. The dubbed video gets translated subtitles and the credit and made-with-AI end card.
Only for the creator's own videos: a dub is a translation, and translating someone else's work needs their permission.
"""
import re
import subprocess
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from backend.config import FFMPEG_BIN
from backend.video_engine import llm_client

RATE = 24000
DUCK = 0.12                 # original soundtrack level under a dubbed line (voice not removed)
DUCK_SEPARATED = 0.7        # music-and-effects level under a dubbed line once the original voice is removed
WORDS_PER_SEC = {"Hindi": 3.0, "English": 2.5}
TRANSLATE_PROMPT = """Translate these lines from a video into natural spoken {language}, written in its own script. Each line shows the
seconds it may last; keep each translation short enough to say in that time (about {wps} words a second). Keep names, brand
names and technical terms the way people say them. Do not add or drop meaning, and do not explain.
Return only JSON: {{"lines": ["...", ...]}} with exactly {n} lines in the same order.
{items}"""


def _clean(t: str) -> str:
    return re.sub(r"<[^>]+>|\{[^}]*\}|\[[^\]]*\]|♪", " ", t or "").strip()


def spoken_lines(subtitles: List[Dict[str, Any]], max_gap: float = 0.5, max_len: float = 10.0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for s in subtitles:
        t = " ".join(_clean(s.get("text", "")).split())
        if not t:
            continue
        if cur and s["start"] - cur["end"] <= max_gap and s["end"] - cur["start"] <= max_len and not re.search(r"[.!?।]$", cur["text"]):
            cur["end"], cur["text"] = s["end"], f"{cur['text']} {t}"
        else:
            if cur:
                out.append(cur)
            cur = {"start": float(s["start"]), "end": float(s["end"]), "text": t}
    if cur:
        out.append(cur)
    return out


def snap_to_words(lines: List[Dict[str, Any]], words: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Transcript segments can include long silences; the word times say when each line is really spoken."""
    if not words:
        return lines
    ws = sorted((float(w["start"]), float(w["end"])) for w in words if str(w.get("word", "")).strip())
    for line in lines:
        inside = [w for w in ws if line["start"] - 0.25 <= w[0] <= line["end"] + 0.25]
        if inside:
            line["start"], line["end"] = inside[0][0], max(inside[-1][1], inside[0][0] + 0.3)
    return lines


def translate(lines: List[Dict[str, Any]], language: str, api_key: Optional[str] = None,
              progress: Callable[[float, str], None] = lambda p, m: None) -> List[Optional[str]]:
    out: List[Optional[str]] = [None] * len(lines)
    batches = [list(range(i, min(len(lines), i + 40))) for i in range(0, len(lines), 40)]
    done = [0]

    def run(batch: List[int]) -> None:
        items = "\n".join(f"{k + 1}. [{lines[i]['end'] - lines[i]['start']:.1f}s] {lines[i]['text']}" for k, i in enumerate(batch))
        prompt = TRANSLATE_PROMPT.format(language=language, wps=WORDS_PER_SEC.get(language, 2.5), n=len(batch), items=items)
        for _ in range(2):
            try:
                got = llm_client.generate_json(prompt, api_key=api_key, temperature=0.3, timeout=240).get("lines") or []
            except Exception:
                continue
            if len(got) == len(batch):
                for i, t in zip(batch, got):
                    out[i] = str(t).strip() or None
                break
        done[0] += 1
        progress(5 + 35 * done[0] / len(batches), f"Translated {done[0]} of {len(batches)} batches...")

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run, batches))
    return out


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip() or 0)
    except ValueError:
        return 0.0


def _to_wav(src: str, dst: str, tempo: float) -> bool:
    af = f"atempo={tempo:.3f}," if tempo > 1.01 else ""
    r = subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-i", src, "-af", f"{af}aresample={RATE}", "-ac", "1", "-c:a", "pcm_s16le", dst],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0


def _srt(lines: List[Dict[str, Any]], texts: List[Optional[str]]) -> str:
    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"
    rows = [(l, t) for l, t in zip(lines, texts) if t]
    return "\n".join(f"{i}\n{ts(l['start'])} --> {ts(l['end'])}\n{t}\n" for i, (l, t) in enumerate(rows, 1))


def dub_video(video_path: str, subtitles: List[Dict[str, Any]], language: str, out_path: str, work_dir: str,
              voice_key: Optional[str] = None, api_key: Optional[str] = None, credit: str = "", title: str = "",
              progress: Optional[Callable[[float, str], None]] = None, words: Optional[List[Dict[str, Any]]] = None,
              remove_voice: bool = True) -> Dict[str, Any]:
    from backend.video_engine.narrator import AVAILABLE_VOICES, default_voice_for_language, synthesize_many, synthesize_voiceover
    from backend.video_engine.endcard import append_end_card
    progress = progress or (lambda p, m: None)
    t0 = time.time()
    work = Path(work_dir)
    (work / "tts").mkdir(parents=True, exist_ok=True)
    lines = snap_to_words(spoken_lines(subtitles), words)
    if not lines:
        raise ValueError("The video has no transcript to dub. Transcribe it first.")
    progress(3, f"Translating {len(lines)} lines into {language}...")
    texts = translate(lines, language, api_key, progress)
    vk = voice_key or default_voice_for_language(language)
    fallback = AVAILABLE_VOICES.get(vk, {}).get("fallback")
    todo = [i for i, t in enumerate(texts) if t]
    mp3 = {i: str(work / "tts" / f"{i:04d}.mp3") for i in todo}
    done = dict(zip(todo, synthesize_many([texts[i] for i in todo], vk, [mp3[i] for i in todo])))
    count = [0]

    def voice(i: int) -> None:
        if not done.get(i):
            for v in (vk, fallback):
                if not v:
                    continue
                try:
                    synthesize_voiceover(texts[i], v, mp3[i])
                except Exception:
                    continue
                if Path(mp3[i]).exists() and Path(mp3[i]).stat().st_size > 0:
                    break
        count[0] += 1
        progress(40 + 40 * count[0] / max(1, len(todo)), f"Voiced {count[0]} of {len(todo)} lines...")

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(voice, todo))
    total = _duration(video_path)
    track = np.zeros(int((total + 2) * RATE), dtype=np.float32)
    sped, overflow = 0, 0
    spans: List[tuple] = []
    for n, i in enumerate(todo):
        if not Path(mp3[i]).exists():
            continue
        start = lines[i]["start"]
        nxt = lines[todo[n + 1]]["start"] if n + 1 < len(todo) else total
        slot = max(0.6, nxt - start - 0.1)
        need = _duration(mp3[i])
        tempo = min(1.3, need / slot) if need > slot else 1.0
        sped += tempo > 1.01
        overflow += need / max(tempo, 1.0) > slot + 0.05
        wav = str(work / "tts" / f"{i:04d}.wav")
        if not _to_wav(mp3[i], wav, tempo):
            continue
        with wave.open(wav, "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        a = int(start * RATE)
        b = min(len(track), a + len(pcm))
        track[a:b] += pcm[: b - a]
        # duck the whole original line (it can outlast its shorter translation) as well as the new voice
        spans.append((lines[i]["start"] - 0.3, max(lines[i]["end"], start + len(pcm) / RATE) + 0.3))
    np.clip(track, -1.0, 1.0, out=track)
    merged: List[list] = []
    for a_t, b_t in sorted(spans):
        if merged and a_t - merged[-1][1] < 0.8:           # bridge short gaps so the music does not pump
            merged[-1][1] = max(merged[-1][1], b_t)
        else:
            merged.append([a_t, b_t])
    spans = merged
    # Original soundtrack: full level between lines, held at DUCK (about -18 dB) for each new line's whole slot with
    # 150 ms fades, so the original voice cannot come through in the dub's pauses; music and effects stay audible.
    env_rate = 1000
    env = np.ones(int((total + 2) * env_rate), dtype=np.float32)
    for a_t, b_t in spans:
        env[max(0, int(a_t * env_rate)): int(b_t * env_rate)] = DUCK
    w_len = int(0.15 * env_rate)
    cs = np.concatenate([[0.0], np.cumsum(np.concatenate([np.full(w_len // 2, env[0]), env, np.full(w_len - w_len // 2, env[-1])]))])
    env = ((cs[w_len:] - cs[:-w_len]) / w_len)[: len(env)].astype(np.float32)
    env_wav = work / "duck.wav"
    with wave.open(str(env_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(env_rate)
        w.writeframes((np.clip(env, 0, 1) * 32767).astype(np.int16).tobytes())
    dub_wav = work / "dub.wav"
    with wave.open(str(dub_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((track * 32767).astype(np.int16).tobytes())
    background, separated, sep_info = None, False, None
    if remove_voice:
        from backend.video_engine import separation
        if separation.available():
            progress(82, "Removing the original voice (Demucs)...")
            try:
                background = str(work / "background.wav")
                sep_info = separation.separate(video_path, background, lambda p, m: progress(82 + p * 0.06, m))
                separated = True
            except Exception as e:          # fall back to lowering the original under the dub
                print(f"Voice removal failed, lowering the original instead: {e}")
                background = None
    if separated:
        # the original voice is gone, so the music only dips a little under the new voice
        env_s = np.clip((env - DUCK) / (1 - DUCK), 0, 1) * (1 - DUCK_SEPARATED) + DUCK_SEPARATED
        with wave.open(str(env_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(env_rate)
            w.writeframes((np.clip(env_s, 0, 1) * 32767).astype(np.int16).tobytes())
    progress(88, "Mixing the new voice over the soundtrack...")
    mix = (("[3:a]" if separated else "[0:a]") + "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[orig];"
           "[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,pan=stereo|c0=c0|c1=c0[env];"
           "[orig][env]amultiply[duck];"
           "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[vo];"
           "[duck][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]")
    r = subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-i", video_path, "-i", str(dub_wav), "-i", str(env_wav)]
                       + (["-i", background] if separated else []) + ["-filter_complex", mix,
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("Mixing the dub failed: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:200])
    srt_path = str(Path(out_path).with_suffix(".srt"))
    Path(srt_path).write_text(_srt(lines, texts), encoding="utf-8")
    progress(95, "Adding the credits card...")
    append_end_card(out_path, credit, title, language)
    return {"file": out_path, "srt": srt_path, "lines": len(lines), "translated": len(todo), "voice": vk, "sped_up": sped,
            "ran_over": overflow, "minutes": round(total / 60, 2), "sec": round(time.time() - t0, 1),
            "original_voice_removed": separated, "separation": sep_info}
