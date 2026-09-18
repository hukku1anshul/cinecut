import json
import os
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Callable

import httpx

from backend.config import FFMPEG_BIN
from backend.video_engine.subtitles_and_chapters import format_srt_timestamp

GROQ_AUDIO_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def is_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        try:
            import whisper  # noqa: F401
            return True
        except ImportError:
            return False


def groq_transcription_available() -> bool:
    from backend.video_engine.llm_usage import env_keys
    return bool(env_keys("GROQ"))


def extract_audio_for_transcription(video_path: str, output_wav_path: str) -> bool:
    """Extracts 16 kHz mono WAV audio, the format Whisper expects."""
    cmd = [FFMPEG_BIN, "-y", "-i", str(video_path), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
           str(output_wav_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return res.returncode == 0 and os.path.exists(output_wav_path)
    except Exception:
        return False


def _write_srt(subs: List[Dict[str, Any]], output_srt_path: str) -> None:
    lines = []
    for idx, s in enumerate(subs, 1):
        lines += [str(idx), f"{format_srt_timestamp(s['start'])} --> {format_srt_timestamp(s['end'])}", s["text"], ""]
    Path(output_srt_path).write_text("\n".join(lines), encoding="utf-8")


def transcribe_video_offline(video_path: str, output_srt_path: str, model_size: str = "base") -> Tuple[bool, List[Dict[str, Any]]]:
    """Transcribes the audio track locally with faster-whisper (or openai-whisper). Returns (ok, subtitles)."""
    wav_path = Path(output_srt_path).with_suffix(".wav")
    if not extract_audio_for_transcription(video_path, str(wav_path)):
        return False, []
    subs: List[Dict[str, Any]] = []
    try:
        try:
            from faster_whisper import WhisperModel
            try:
                model = WhisperModel(model_size, device="cuda", compute_type="float16")
            except Exception:
                model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, _ = model.transcribe(str(wav_path), beam_size=5, vad_filter=True)
            for seg in segments:
                text = seg.text.strip()
                if text:
                    subs.append({"start": round(seg.start, 2), "end": round(seg.end, 2),
                                 "duration": round(seg.end - seg.start, 2), "text": text})
        except ImportError:
            import whisper
            model = whisper.load_model(model_size)
            for seg in model.transcribe(str(wav_path)).get("segments", []):
                text = seg["text"].strip()
                if text:
                    subs.append({"start": round(seg["start"], 2), "end": round(seg["end"], 2),
                                 "duration": round(seg["end"] - seg["start"], 2), "text": text})
        if subs:
            _write_srt(subs, output_srt_path)
            if words:   # word timings for animated reel captions
                Path(output_srt_path).with_suffix(".words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        return bool(subs), subs
    except Exception as e:
        print(f"Offline transcription failed: {e}")
        return False, []
    finally:
        if wav_path.exists():
            try:
                wav_path.unlink()
            except OSError:
                pass


def transcribe_with_groq(video_path: str, output_srt_path: str, language: Optional[str] = None,
                         progress: Optional[Callable[[float, str], None]] = None,
                         chunk_sec: int = 600) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Cloud transcription with Groq's free-tier Whisper large-v3. The audio (not the video) is split into
    10-minute 32 kbps mono chunks, well under the free tier's file-size limit, and sent one by one. When the
    free tier's audio-per-hour limit is reached, it waits and continues. Returns (ok, subtitles, message).
    """
    from backend.video_engine import llm_usage
    from backend.video_engine.probe import get_media_duration

    keys = llm_usage.env_keys("GROQ")
    if not keys:
        return False, [], "No GROQ_API_KEY in the .env file."
    work = Path(output_srt_path).parent / (Path(output_srt_path).stem + "_chunks")
    work.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda p, m: None)
    report(3, "Extracting the audio track...")
    cmd = [FFMPEG_BIN, "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame",
           "-b:a", "32k", "-f", "segment", "-segment_time", str(chunk_sec), "-reset_timestamps", "1",
           str(work / "chunk_%03d.mp3")]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunks = sorted(work.glob("chunk_*.mp3"))
    if not chunks:
        return False, [], "Could not extract the audio track."

    subs: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []
    failed_parts: List[int] = []
    offset = 0.0
    # Groq's free tier limits audio per hour for each model; when one model is limited, the other is used
    models = ["whisper-large-v3", "whisper-large-v3-turbo"]
    blocked = {}
    try:
        for i, chunk in enumerate(chunks):
            chunk_len = get_media_duration(str(chunk)) or float(chunk_sec)
            if chunk_len < 2.0:
                offset += chunk_len  # a few-millisecond tail left by the splitter; Groq rejects it
                continue
            report(5 + 90 * i / len(chunks), f"Transcribing part {i + 1} of {len(chunks)} with Groq Whisper...")
            for attempt in range(40):
                # every Groq key with every Whisper model, each within its own audio-per-hour and per-day allowance
                combo = next(((k, m) for m in models for k in keys if llm_usage.available("groq_audio", k, m, units=chunk_len)), None)
                if combo is None:
                    wait = llm_usage.wait_hint("groq_audio", keys, models, units=chunk_len)
                    if wait is None or wait > 3600:
                        return False, subs, ("Today's free Groq audio allowance is used up on every key. It resets at midnight UTC; "
                                             "adding more Groq keys (GROQ_API_KEY_2, ...) raises the daily total.")
                    report(5 + 90 * i / len(chunks), f"Every Groq key is at its audio limit for now; waiting {int(wait)} s before part {i + 1}...")
                    time.sleep(max(1.0, wait))
                    continue
                key, model = combo
                data = {"model": model, "response_format": "verbose_json", "temperature": "0",
                        "timestamp_granularities[]": ["word", "segment"]}
                if language:
                    data["language"] = language
                with open(chunk, "rb") as f:
                    res = httpx.post(GROQ_AUDIO_URL, headers={"Authorization": f"Bearer {key}"}, data=data,
                                     files={"file": (chunk.name, f, "audio/mpeg")}, timeout=300)
                if res.status_code == 429:
                    try:
                        wait = float(res.headers.get("retry-after", 30))
                    except ValueError:
                        wait = 30.0
                    llm_usage.note_429("groq_audio", key, model, res.text[:800], max(5.0, wait))
                    continue
                if res.status_code >= 500:
                    wait = min(60.0, 10.0 * (attempt + 1))
                    report(5 + 90 * i / len(chunks), f"Groq had a temporary error; retrying part {i + 1} in {int(wait)} s...")
                    time.sleep(wait)
                    continue
                if res.status_code != 200:
                    return False, subs, f"Groq error {res.status_code}: {res.text[:200]}"
                llm_usage.record("groq_audio", key, model=model, units=chunk_len)
                detected = str(res.json().get("language", "")).lower()
                if language is None and detected in ("urdu", "ur"):
                    language = "hi"  # Hindi film dialogue is often detected as Urdu; ask for Devanagari text
                    continue
                last = None
                for seg in res.json().get("segments", []):
                    text = " ".join(str(seg.get("text", "")).split())
                    if not text or seg.get("no_speech_prob", 0) > 0.7 or text == last:
                        continue
                    last = text
                    start, end = offset + float(seg["start"]), offset + float(seg["end"])
                    subs.append({"start": round(start, 2), "end": round(end, 2), "duration": round(end - start, 2), "text": text})
                for w in res.json().get("words") or []:
                    try:
                        ws, we = offset + float(w["start"]), offset + float(w["end"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if str(w.get("word", "")).strip():
                        words.append({"word": str(w["word"]).strip(), "start": round(ws, 2), "end": round(we, 2)})
                break
            else:
                failed_parts.append(i + 1)  # keep going; one bad part should not throw away the rest
            offset += chunk_len
        if subs:
            _write_srt(subs, output_srt_path)
            if words:        # word times drive karaoke captions and line up dubs with the real speech
                Path(output_srt_path).with_suffix(".words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        note = f" Part(s) {', '.join(map(str, failed_parts))} could not be transcribed; try again later." if failed_parts else ""
        return bool(subs), subs, f"Transcribed {len(subs)} lines with Groq Whisper (free tier).{note}"
    finally:
        for c in chunks:
            try:
                c.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass
