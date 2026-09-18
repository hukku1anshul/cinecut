"""
Extra narration voices (each returns True after writing an MP3, or False so the caller falls back to Edge):
  - Gemini speech: expressive narrators on the free tier of the Gemini keys in .env (Hindi, English and more).
  - Sarvam Bulbul v3: Indian voices, when SARVAM_API_KEY is set in .env (Sarvam gives free starter credits).
  - AI4Bharat Indic Parler-TTS: runs on the laptop GPU once the model is downloaded. It is gated on Hugging Face:
    accept its licence with your account, then put HF_TOKEN in .env and install `parler-tts` (see README).
"""
import base64
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from backend.config import FFMPEG_BIN, GEMINI_API_BASE
from backend.video_engine import llm_usage
from backend.video_engine.gemini_client import _retry_after

SARVAM_LANGS = {"Hindi": "hi-IN", "English": "en-IN", "Bengali": "bn-IN", "Tamil": "ta-IN", "Telugu": "te-IN", "Marathi": "mr-IN",
                "Gujarati": "gu-IN", "Kannada": "kn-IN", "Malayalam": "ml-IN", "Punjabi": "pa-IN", "Odia": "od-IN"}

GEMINI_TTS_MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts")
STYLE = {
    "Hindi": "Read this aloud in Hindi as a warm, clear film-recap narrator, at a relaxed, steady pace:",
    "English": "Read this aloud as a warm, clear film-recap narrator, at a relaxed, steady pace:",
}
_blocked = {}
_parler = {"model": None, "lock": threading.Lock(), "error": None}


def _to_mp3(src: Path, out_path: str, raw_rate: Optional[int] = None) -> bool:
    cmd = [FFMPEG_BIN, "-y", "-v", "error"]
    if raw_rate:
        cmd += ["-f", "s16le", "-ar", str(raw_rate), "-ac", "1"]
    cmd += ["-i", str(src), "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "96k", out_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    try:
        src.unlink()
    except OSError:
        pass
    return res.returncode == 0 and os.path.exists(out_path)


# ------------------------------------------------------------------------------------------ Gemini
def gemini_available() -> bool:
    return bool(llm_usage.env_keys("GEMINI") or llm_usage.env_keys("GOOGLE"))


def gemini_tts(text: str, voice_name: str, out_path: str, language: str = "Hindi", timeout: float = 90.0,
               style: Optional[str] = None, models: Optional[tuple] = None, speakers: Optional[list] = None) -> bool:
    keys = llm_usage.env_keys("GEMINI") + llm_usage.env_keys("GOOGLE")
    prompt = f"{style}\n\n{text}" if style else f"{STYLE.get(language, STYLE['English'])} {text}"
    for key in keys:
        if not llm_usage.available("gemini_tts", key):
            continue
        kid = llm_usage.key_id("gemini", key)
        for model in models or GEMINI_TTS_MODELS:
            slot = f"{kid}|{model}"
            if _blocked.get(slot, 0) > time.time() or not llm_usage.available("gemini_tts", key, model):
                continue
            if speakers:        # two voices in one take: the text has "Name: ..." turns
                speech = {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
                    {"speaker": name, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v}}} for name, v in speakers]}}
            else:
                speech = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}
            body = {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": speech}}
            try:
                res = httpx.post(f"{GEMINI_API_BASE}/{model}:generateContent", json=body,
                                 headers={"x-goog-api-key": key}, timeout=timeout)
            except httpx.HTTPError:
                continue
            if res.status_code == 429 or "RESOURCE_EXHAUSTED" in res.text[:800]:
                llm_usage.note_429("gemini_tts", key, model, res.text[:1500], _retry_after(res))
                continue
            if res.status_code != 200:
                _blocked[slot] = time.time() + (24 * 3600 if res.status_code in (400, 404) else 30)
                continue
            try:
                part = next(p for p in res.json()["candidates"][0]["content"]["parts"] if p.get("inlineData"))["inlineData"]
            except (KeyError, IndexError, StopIteration, ValueError):
                continue
            rate = int((re.search(r"rate=(\d+)", part.get("mimeType", "")) or [None, "24000"])[1])
            raw = Path(out_path).with_suffix(".pcm")
            raw.write_bytes(base64.b64decode(part["data"]))
            llm_usage.record("gemini_tts", key, model=model)
            return _to_mp3(raw, out_path, raw_rate=rate)
    return False


# ------------------------------------------------------------------------------------------ Sarvam
def sarvam_available() -> bool:
    """A Sarvam key is set and not resting (out of credits, or over its rate limit). While it rests every caller picks
    another voice from the start, so a title or a dub is never half Sarvam and half Edge."""
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        return False
    try:
        return llm_usage.free_in("sarvam", key) <= 0
    except Exception:
        return True


def sarvam_tts(text: str, speaker: str, out_path: str, language_code: str = "hi-IN", timeout: float = 60.0) -> bool:
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        return False
    try:
        res = httpx.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": key},
                         json={"text": text[:2400], "language_code": language_code, "speaker": speaker, "model": "bulbul:v3",
                               "pace": 1.0, "speech_sample_rate": "24000", "output_audio_codec": "wav"}, timeout=timeout)
    except httpx.HTTPError:
        return False
    if res.status_code != 200:
        print(f"Sarvam TTS error {res.status_code}: {res.text[:200]}")
        if res.status_code == 402 or "insufficient_quota" in res.text:
            llm_usage.cool_down("sarvam", key, 6 * 3600, why="no credits left")    # until the account is topped up
        elif res.status_code == 429:
            llm_usage.cool_down("sarvam", key, 60, why="rate limit")
        return False
    audios = res.json().get("audios") or []
    if not audios:
        return False
    wav = Path(out_path).with_suffix(".wav")
    wav.write_bytes(base64.b64decode(audios[0]))
    return _to_mp3(wav, out_path)


# ------------------------------------------------------------------------------------------ Indic Parler-TTS (local)
PARLER_REPO = "ai4bharat/indic-parler-tts"


def parler_status() -> str:
    import importlib.util
    if not (importlib.util.find_spec("torch") and importlib.util.find_spec("parler_tts")):   # no heavy imports here
        return "not installed (needs: pip install parler-tts transformers soundfile)"
    try:
        from huggingface_hub import try_to_load_from_cache
        cached = try_to_load_from_cache(PARLER_REPO, "model.safetensors")
        if isinstance(cached, str):
            return "ready"
    except Exception:
        pass
    token = os.environ.get("HF_TOKEN")
    if not token:
        return "needs HF_TOKEN in .env (the model is gated on Hugging Face)"
    now = time.time()
    if _parler.get("access_checked", 0) + 600 < now:     # ask Hugging Face at most every 10 minutes
        _parler["access_checked"] = now
        try:
            from huggingface_hub import hf_hub_download
            hf_hub_download(PARLER_REPO, "config.json", token=token)
            _parler["access"] = "ready to download"
        except Exception as e:
            _parler["access"] = ("needs you to accept the model's licence at huggingface.co/ai4bharat/indic-parler-tts"
                                 if "Gated" in type(e).__name__ or "403" in str(e) else f"cannot reach Hugging Face ({type(e).__name__})")
    return _parler.get("access", "ready to download")


def parler_available() -> bool:
    return parler_status() in ("ready", "ready to download")


def parler_tts(text: str, speaker: str, out_path: str) -> bool:
    if not parler_available():
        return False
    try:
        import torch
        import soundfile as sf
        from parler_tts import ParlerTTSForConditionalGeneration
        from transformers import AutoTokenizer
        with _parler["lock"]:
            if _parler["model"] is None:
                token = os.environ.get("HF_TOKEN")
                device = "cuda" if torch.cuda.is_available() else "cpu"
                dtype = torch.float16 if device == "cuda" else torch.float32
                model = ParlerTTSForConditionalGeneration.from_pretrained(PARLER_REPO, token=token, torch_dtype=dtype).to(device)
                tok = AutoTokenizer.from_pretrained(PARLER_REPO, token=token)
                desc_tok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path, token=token)
                _parler["model"] = (model, tok, desc_tok, device)
            model, tok, desc_tok, device = _parler["model"]
            description = (f"{speaker}'s voice is warm and clear, with a moderate pace and slightly expressive tone, "
                           "recorded in a quiet room with very clear audio.")
            d = desc_tok(description, return_tensors="pt").to(device)
            p = tok(text, return_tensors="pt").to(device)
            with torch.no_grad():
                audio = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                                       prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask)
            wav = Path(out_path).with_suffix(".wav")
            sf.write(str(wav), audio.to(torch.float32).cpu().numpy().squeeze(), model.config.sampling_rate)
        return _to_mp3(wav, out_path)
    except Exception as e:
        _parler["error"] = str(e)
        print(f"Indic Parler-TTS failed: {e}")
        return False


def parler_tts_batch(texts, speaker: str, out_paths, per_batch: int = 6):
    """Voices several lines in one GPU pass (padded batch), which is much faster than one line at a time."""
    done = [False] * len(texts)
    if not parler_available() or not texts:
        return done
    try:
        import torch
        import soundfile as sf
        if not parler_tts(texts[0], speaker, out_paths[0]):      # loads the model on first use
            return done
        done[0] = True
        model, tok, desc_tok, device = _parler["model"]
        description = (f"{speaker}'s voice is warm and clear, with a moderate pace and slightly expressive tone, "
                       "recorded in a quiet room with very clear audio.")
        rest = list(range(1, len(texts)))
        for g in range(0, len(rest), per_batch):
            idx = rest[g:g + per_batch]
            with _parler["lock"]:
                d = desc_tok([description] * len(idx), return_tensors="pt", padding=True).to(device)
                pr = tok([texts[i] for i in idx], return_tensors="pt", padding=True).to(device)
                with torch.no_grad():
                    gen = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                                         prompt_input_ids=pr.input_ids, prompt_attention_mask=pr.attention_mask,
                                         return_dict_in_generate=True)
            for row, i in enumerate(idx):
                length = int(gen.audios_length[row]) if getattr(gen, "audios_length", None) is not None else None
                audio = gen.sequences[row, :length] if length else gen.sequences[row]
                wav = Path(out_paths[i]).with_suffix(".wav")
                sf.write(str(wav), audio.to(torch.float32).cpu().numpy().squeeze(), model.config.sampling_rate)
                done[i] = _to_mp3(wav, out_paths[i])
    except Exception as e:
        _parler["error"] = str(e)
        print(f"Indic Parler-TTS batch failed: {e}")
    return done


def synthesize(engine: str, name: str, text: str, out_path: str, language: str) -> bool:
    if engine == "gemini":
        return gemini_tts(text, name, out_path, language)
    if engine == "sarvam":
        return sarvam_tts(text, name, out_path, SARVAM_LANGS.get(language, "en-IN"))
    if engine == "parler":
        return parler_tts(text, name, out_path)
    return False



def _silences(path: str, noise_db: int = -38, min_len: float = 0.8):
    res = subprocess.run([FFMPEG_BIN, "-hide_banner", "-nostats", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", path,
                          "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", res.stderr)]
    return list(zip(starts, ends))


def gemini_tts_batch(texts, voice_name: str, out_paths, language: str = "Hindi", per_request: int = 6):
    """Reads up to `per_request` lines per request with long pauses between them, then cuts the audio at the pauses.
    A group whose pauses cannot be matched to its lines is left for the fallback voice."""
    done = [False] * len(texts)
    keys = llm_usage.env_keys("GEMINI") + llm_usage.env_keys("GOOGLE")
    for g in range(0, len(texts), per_request):
        group = list(range(g, min(len(texts), g + per_request)))
        numbered = "\n".join(f"{n + 1}. {texts[i]}" for n, i in enumerate(group))
        prompt = (f"{STYLE.get(language, STYLE['English'])}\nRead each numbered line below, in order. Do not read the numbers. "
                  f"After each line, stay completely silent for two full seconds.\n{numbered}")
        audio = None
        for key in keys:
            if audio or not llm_usage.available("gemini_tts", key):
                continue
            kid = llm_usage.key_id("gemini", key)
            for model in GEMINI_TTS_MODELS:
                slot = f"{kid}|{model}"
                if _blocked.get(slot, 0) > time.time() or not llm_usage.available("gemini_tts", key, model):
                    continue
                body = {"contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseModalities": ["AUDIO"],
                                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}}}
                try:
                    res = httpx.post(f"{GEMINI_API_BASE}/{model}:generateContent", json=body, headers={"x-goog-api-key": key}, timeout=240)
                except httpx.HTTPError:
                    continue
                if res.status_code == 429 or "RESOURCE_EXHAUSTED" in res.text[:800]:
                    llm_usage.note_429("gemini_tts", key, model, res.text[:1500], _retry_after(res))
                    continue
                if res.status_code != 200:
                    _blocked[slot] = time.time() + (24 * 3600 if res.status_code in (400, 404) else 60)
                    continue
                try:
                    part = next(p for p in res.json()["candidates"][0]["content"]["parts"] if p.get("inlineData"))["inlineData"]
                    audio = base64.b64decode(part["data"])
                    llm_usage.record("gemini_tts", key, model=model)
                    break
                except Exception:
                    continue
        if not audio:
            continue
        raw = Path(out_paths[group[0]]).with_suffix(".batch.pcm")
        raw.write_bytes(audio)
        total = len(audio) / 2 / 24000.0
        gaps = sorted(_silences(str(raw)), key=lambda x: -(x[1] - x[0]))[: len(group) - 1]
        gaps = sorted(gaps)
        if len(gaps) == len(group) - 1:
            bounds = [0.0] + [(a + b) / 2 for a, b in gaps] + [total]
            for n, i in enumerate(group):
                a, b = bounds[n], bounds[n + 1]
                if b - a < 0.6:
                    continue
                cmd = [FFMPEG_BIN, "-y", "-v", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(raw),
                       "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-af", "silenceremove=start_periods=1:start_threshold=-45dB,"
                       "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                       "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "96k", str(out_paths[i])]
                ok = subprocess.run(cmd, capture_output=True).returncode == 0 and os.path.exists(out_paths[i])
                done[i] = ok
        try:
            raw.unlink()
        except OSError:
            pass
    return done
