import asyncio
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from xml.sax.saxutils import escape as xml_escape

import httpx

from backend.config import (FFMPEG_BIN, VOICEOVER_LEAD_SEC, VOICEOVER_DUCK_LEVEL, VOICEOVER_GAIN,
                            AUDIO_SAMPLE_RATE)

try:
    import edge_tts  # unofficial Microsoft Edge read-aloud client (personal use; needs internet)
except ImportError:  # pragma: no cover
    edge_tts = None

AVAILABLE_VOICES = {
    "christopher": {"id": "en-US-ChristopherNeural", "name": "Christopher (US English - Cinematic)", "language": "English", "lang_code": "en"},
    "guy": {"id": "en-US-GuyNeural", "name": "Guy (US English - Dramatic)", "language": "English", "lang_code": "en"},
    "en_in_prabhat": {"id": "en-IN-PrabhatNeural", "name": "Prabhat (Indian English)", "language": "English", "lang_code": "en"},
    "en_in_neerja": {"id": "en-IN-NeerjaNeural", "name": "Neerja (Indian English, female)", "language": "English", "lang_code": "en"},
    "hi_madhur": {"id": "hi-IN-MadhurNeural", "name": "Madhur (हिन्दी / Hindi, male)", "language": "Hindi", "lang_code": "hi"},
    "hi_swara": {"id": "hi-IN-SwaraNeural", "name": "Swara (हिन्दी / Hindi, female)", "language": "Hindi", "lang_code": "hi"},
    "es_jorge": {"id": "es-MX-JorgeNeural", "name": "Jorge (Español / Mexican Spanish)", "language": "Spanish", "lang_code": "es"},
    "fr_henri": {"id": "fr-FR-HenriNeural", "name": "Henri (Français / French)", "language": "French", "lang_code": "fr"},
    "de_conrad": {"id": "de-DE-ConradNeural", "name": "Conrad (Deutsch / German)", "language": "German", "lang_code": "de"},
    "ja_keita": {"id": "ja-JP-KeitaNeural", "name": "Keita (日本語 / Japanese)", "language": "Japanese", "lang_code": "ja"},
}

# Expressive and Indian voices (tts_engines.py). Gemini works with the Gemini keys in .env; Sarvam needs
# SARVAM_API_KEY; Indic Parler-TTS runs on this laptop once its gated model is available. Each falls back to Edge.
AVAILABLE_VOICES.update({
    "hi_gemini_charon": {"id": "gemini:Charon", "name": "Charon (Hindi, expressive AI narrator, Gemini free tier: about 100 lines a day)", "language": "Hindi",
                         "lang_code": "hi", "engine": "gemini", "fallback": "hi_madhur"},
    "hi_gemini_kore": {"id": "gemini:Kore", "name": "Kore (Hindi, expressive AI narrator, female, Gemini free tier: about 100 lines a day)", "language": "Hindi",
                       "lang_code": "hi", "engine": "gemini", "fallback": "hi_swara"},
    "en_gemini_charon": {"id": "gemini:Charon", "name": "Charon (English, expressive AI narrator, Gemini free tier: about 100 lines a day)", "language": "English",
                         "lang_code": "en", "engine": "gemini", "fallback": "christopher"},
    "en_gemini_aoede": {"id": "gemini:Aoede", "name": "Aoede (English, expressive AI narrator, female, Gemini free tier: about 100 lines a day)", "language": "English",
                        "lang_code": "en", "engine": "gemini", "fallback": "en_in_neerja"},
    "hi_sarvam_shubh": {"id": "sarvam:shubh", "name": "Shubh (Hindi, Sarvam Bulbul v3, uses your Sarvam credits)", "language": "Hindi", "lang_code": "hi",
                        "engine": "sarvam", "fallback": "hi_madhur"},
    "hi_sarvam_ritu": {"id": "sarvam:ritu", "name": "Ritu (Hindi, Sarvam Bulbul v3, female, uses your Sarvam credits)", "language": "Hindi", "lang_code": "hi",
                       "engine": "sarvam", "fallback": "hi_swara"},
    "hi_parler_rohit": {"id": "parler:Rohit", "name": "Rohit (Hindi, Indic Parler-TTS on this laptop)", "language": "Hindi",
                        "lang_code": "hi", "engine": "parler", "fallback": "hi_madhur"},
    "hi_parler_divya": {"id": "parler:Divya", "name": "Divya (Hindi, Indic Parler-TTS on this laptop, female)", "language": "Hindi",
                        "lang_code": "hi", "engine": "parler", "fallback": "hi_swara"},
})
# Indian English and regional languages: Sarvam Bulbul v3 speaks all of them (SARVAM_API_KEY); Edge is the fallback.
AVAILABLE_VOICES.update({
    "en_sarvam_shubh": {"id": "sarvam:shubh", "name": "Shubh (Indian English, Sarvam Bulbul v3)", "language": "English", "lang_code": "en",
                        "engine": "sarvam", "fallback": "en_in_prabhat"},
    "bn_edge": {"id": "bn-IN-BashkarNeural", "name": "Bashkar (বাংলা / Bengali)", "language": "Bengali", "lang_code": "bn"},
    "ta_edge": {"id": "ta-IN-ValluvarNeural", "name": "Valluvar (தமிழ் / Tamil)", "language": "Tamil", "lang_code": "ta"},
    "te_edge": {"id": "te-IN-MohanNeural", "name": "Mohan (తెలుగు / Telugu)", "language": "Telugu", "lang_code": "te"},
    "mr_edge": {"id": "mr-IN-ManoharNeural", "name": "Manohar (मराठी / Marathi)", "language": "Marathi", "lang_code": "mr"},
    "gu_edge": {"id": "gu-IN-NiranjanNeural", "name": "Niranjan (ગુજરાતી / Gujarati)", "language": "Gujarati", "lang_code": "gu"},
    "kn_edge": {"id": "kn-IN-GaganNeural", "name": "Gagan (ಕನ್ನಡ / Kannada)", "language": "Kannada", "lang_code": "kn"},
    "ml_edge": {"id": "ml-IN-MidhunNeural", "name": "Midhun (മലയാളം / Malayalam)", "language": "Malayalam", "lang_code": "ml"},
})
for _lang, _code, _edge in (("Bengali", "bn", "bn_edge"), ("Tamil", "ta", "ta_edge"), ("Telugu", "te", "te_edge"), ("Marathi", "mr", "mr_edge"),
                            ("Gujarati", "gu", "gu_edge"), ("Kannada", "kn", "kn_edge"), ("Malayalam", "ml", "ml_edge"), ("Punjabi", "pa", "hi_madhur")):
    AVAILABLE_VOICES[f"{_code}_sarvam_shubh"] = {"id": "sarvam:shubh", "name": f"Shubh ({_lang}, Sarvam Bulbul v3)", "language": _lang,
                                                 "lang_code": _code, "engine": "sarvam", "fallback": _edge}
REGIONAL_LANGUAGES = ("Bengali", "Tamil", "Telugu", "Marathi", "Gujarati", "Kannada", "Malayalam", "Punjabi")
USED_ENGINE: Dict[str, str] = {}   # output file -> engine that actually produced it


def _mark_engine(path, engine: str) -> None:
    USED_ENGINE[str(path)] = engine
    try:
        Path(str(path) + ".engine").write_text(engine, encoding="utf-8")
    except OSError:
        pass


def engine_of(path) -> str:
    """Which engine produced a narration file (kept next to it, so cached files are known too)."""
    if str(path) in USED_ENGINE:
        return USED_ENGINE[str(path)]
    try:
        return Path(str(path) + ".engine").read_text(encoding="utf-8").strip() or "edge"
    except OSError:
        return "edge"


def voice_ready(voice_key: str) -> bool:
    from backend.video_engine import tts_engines
    engine = AVAILABLE_VOICES.get(voice_key, {}).get("engine", "edge")
    return {"gemini": tts_engines.gemini_available, "sarvam": tts_engines.sarvam_available,
            "parler": tts_engines.parler_available}.get(engine, lambda: True)()


LANGUAGE_DEFAULT_VOICE = {
    "English": "christopher", "Hindi": "hi_madhur", "Spanish": "es_jorge",
    "French": "fr_henri", "German": "de_conrad", "Japanese": "ja_keita",
    "Bengali": "bn_edge", "Tamil": "ta_edge", "Telugu": "te_edge", "Marathi": "mr_edge", "Gujarati": "gu_edge",
    "Kannada": "kn_edge", "Malayalam": "ml_edge", "Punjabi": "pa_sarvam_shubh"
}

INTRO_TEMPLATES = {
    "English": "Here is the story of {title}, condensed.",
    "Hindi": "पेश है {title} की कहानी, संक्षेप में।",
    "Spanish": "Esta es la historia de {title}, en versión condensada.",
    "French": "Voici l'histoire de {title}, en version condensée.",
    "German": "Hier ist die Geschichte von {title}, kompakt erzählt.",
    "Japanese": "{title}の物語を、ぎゅっと凝縮してお届けします。",
}

LOCALIZED_FALLBACKS = {
    "Spanish": ["Mientras las tensiones aumentan, la situación da un giro inesperado.",
                "A medida que el misterio se profundiza, salen a la luz revelaciones cruciales.",
                "Con el tiempo agotándose, la confrontación se vuelve inevitable.",
                "Mientras tanto, una decisión crítica cambia el rumbo de los acontecimientos."],
    "Hindi": ["जैसे-जैसे तनाव बढ़ता है, कहानी एक अप्रत्याशित मोड़ लेती है।",
              "रहस्य गहराने के साथ, महत्वपूर्ण सच्चाई सामने आती है।",
              "समय कम होने के कारण, टकराव अब अपरिहार्य हो जाता है।",
              "इस बीच, एक बड़ा फैसला पूरी कहानी को बदल देता है।"],
    "French": ["Alors que la tension monte, la situation prend une tournure inattendue.",
               "À mesure que le mystère s'épaissit, des révélations cruciales apparaissent.",
               "Le temps pressant, la confrontation devient inévitable.",
               "Pendant ce temps, un choix décisif bouleverse le cours des événements."],
    "German": ["Während die Spannungen steigen, nimmt die Lage eine unerwartete Wendung.",
               "Als sich das Geheimnis vertieft, kommen entscheidende Enthüllungen ans Licht.",
               "Die Zeit drängt, und die Konfrontation wird unvermeidlich.",
               "In der Zwischenzeit verändert eine folgenschwere Entscheidung alles."],
    "Japanese": ["緊張が高まる中、事態は予期せぬ展開を見せる。",
                 "謎が深まるにつれ、決定的な真実が明らかになっていく。",
                 "時間が迫る中、最後の対決は避けられないものとなる。",
                 "その頃、ある重大な決断が運命を大きく狂わせていた。"],
    "English": ["With tensions mounting, the situation takes an unexpected turn.",
                "As the mystery deepens, key revelations begin to surface.",
                "With time running out, the confrontation becomes inevitable.",
                "Meanwhile, a critical decision alters the course of events."]
}

LECTURE_TEMPLATES = {
    "English": "Next, the lecture moves on to {topic}.",
    "Hindi": "अब व्याख्यान {topic} की ओर बढ़ता है।",
    "Spanish": "A continuación, la clase pasa a {topic}.",
    "French": "Ensuite, le cours aborde {topic}.",
    "German": "Als Nächstes geht es in der Vorlesung um {topic}.",
    "Japanese": "次に、講義は{topic}へと進みます。",
}


def default_voice_for_language(language: str) -> str:
    """Hindi uses Sarvam's Indian voice when SARVAM_API_KEY is set (Edge takes over if its credits run out);
    otherwise Edge. Gemini speech is not a default: its free tier allows only about 10 requests a day per model."""
    sarvam = next((k for k, v in AVAILABLE_VOICES.items() if v.get("engine") == "sarvam" and v["language"] == language), None)
    if language != "English" and sarvam and voice_ready(sarvam):
        return sarvam
    return LANGUAGE_DEFAULT_VOICE.get(language, "christopher")


SCRIPT_RANGES = (("Hindi", "ऀ-ॿ"), ("Bengali", "ঀ-৿"), ("Punjabi", "਀-੿"),
                 ("Gujarati", "઀-૿"), ("Tamil", "஀-௿"), ("Telugu", "ఀ-౿"),
                 ("Kannada", "ಀ-೿"), ("Malayalam", "ഀ-ൿ"), ("Japanese", "぀-ヿ一-鿿"))
SAME_SCRIPT = {"Marathi": "Hindi"}      # Marathi is written in Devanagari


def detect_text_language(text: str) -> str:
    for name, rng in SCRIPT_RANGES:
        if re.search(f"[{rng}]", text or ""):
            return name
    return "English"


def voice_for_text(text: str, preferred: Optional[str] = None) -> str:
    """Uses the preferred voice unless the text is clearly in another script (e.g. Hindi text, English voice)."""
    lang = detect_text_language(text)
    if preferred in AVAILABLE_VOICES:
        pref_lang = AVAILABLE_VOICES[preferred]["language"]
        if lang == "English" or pref_lang == lang or SAME_SCRIPT.get(pref_lang) == lang:
            return preferred
    return default_voice_for_language(lang)


def tts_cache_path(directory: Path, text: str, voice_key: str, rate: str = "+0%", engine: str = "edge") -> Path:
    digest = hashlib.sha1(f"{engine}|{voice_key}|{rate}|{text}".encode("utf-8")).hexdigest()[:16]
    return Path(directory) / f"vo_{digest}.mp3"


def _azure_settings(azure_key: Optional[str], azure_region: Optional[str]):
    key = azure_key or os.environ.get("CINECUT_AZURE_SPEECH_KEY")
    region = azure_region or os.environ.get("CINECUT_AZURE_SPEECH_REGION")
    return (key, region) if key and region else (None, None)


def _synthesize_azure(text: str, voice_id: str, output_path: str, key: str, region: str, rate: str = "+0%") -> None:
    """Official Azure AI Speech neural TTS (licensed for commercial use under your Azure agreement)."""
    lang = "-".join(voice_id.split("-")[:2])
    ssml = (f"<speak version='1.0' xml:lang='{lang}'><voice name='{voice_id}'>"
            f"<prosody rate='{rate}'>{xml_escape(text)}</prosody></voice></speak>")
    res = httpx.post(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                     content=ssml.encode("utf-8"), timeout=60.0,
                     headers={"Ocp-Apim-Subscription-Key": key,
                              "Content-Type": "application/ssml+xml",
                              "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
                              "User-Agent": "CineCut"})
    if res.status_code != 200:
        raise RuntimeError(f"Azure Speech error {res.status_code}: {res.text[:200]}")
    Path(output_path).write_bytes(res.content)


async def _synthesize_edge(text: str, voice_id: str, output_path: str, rate: str = "+0%") -> None:
    if edge_tts is None:
        raise RuntimeError("edge-tts is not installed. Run: pip install edge-tts")
    communicate = edge_tts.Communicate(text, voice_id, rate=rate)
    await communicate.save(output_path)


async def synthesize_voiceover_async(text: str, voice_key: str = "christopher", output_path: Optional[str] = None,
                                     rate: str = "+0%", azure_key: Optional[str] = None,
                                     azure_region: Optional[str] = None) -> str:
    """Speech synthesis. Uses Azure AI Speech when a key/region is configured, otherwise Edge TTS."""
    if not output_path:
        raise ValueError("output_path must be provided")
    info = AVAILABLE_VOICES.get(voice_key, AVAILABLE_VOICES["christopher"])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    engine = info.get("engine", "edge")
    if engine != "edge":
        from backend.video_engine import tts_engines
        ok = await asyncio.to_thread(tts_engines.synthesize, engine, info["id"].split(":", 1)[1], text, str(output_path),
                                     info["language"])
        if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 200:
            _mark_engine(output_path, engine)
            return output_path
        info = AVAILABLE_VOICES[info.get("fallback", "christopher")]      # quota or setup missing: use Edge
    voice_id = info["id"]
    key, region = _azure_settings(azure_key, azure_region)
    try:
        if key:
            await asyncio.to_thread(_synthesize_azure, text, voice_id, output_path, key, region, rate)
        else:
            await _synthesize_edge(text, voice_id, output_path, rate)
    except Exception as e:
        engine = "Azure AI Speech" if key else "Edge TTS (needs an internet connection)"
        raise RuntimeError(f"Voice synthesis failed via {engine}: {e}") from e
    if not os.path.exists(output_path) or os.path.getsize(output_path) < 200:
        raise RuntimeError("Voice synthesis produced an empty audio file.")
    _mark_engine(output_path, "azure" if key else "edge")
    return output_path


def synthesize_voiceover(text: str, voice_key: str = "christopher", output_path: Optional[str] = None,
                         rate: str = "+0%", azure_key: Optional[str] = None, azure_region: Optional[str] = None) -> str:
    """Synchronous wrapper that is safe to call from both plain threads and inside a running event loop."""
    coro_args = (text, voice_key, output_path, rate, azure_key, azure_region)
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if running:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(synthesize_voiceover_async(*coro_args))).result()
    return asyncio.run(synthesize_voiceover_async(*coro_args))


def pick_scene_narration(scene: Dict[str, Any], language: str) -> Optional[str]:
    """Hand-written narration from the essence database, when it exists in the requested language."""
    if scene.get("is_continuation"):
        return None
    if language == "Hindi" and scene.get("narration_hi"):
        return scene["narration_hi"]
    if language == "English" and scene.get("narration_en"):
        return scene["narration_en"]
    return None


def generate_ai_bridge_narrations(
    scenes: List[Dict[str, Any]],
    movie_title: str = "the movie",
    gemini_api_key: Optional[str] = None,
    language: str = "English",
    content_mode: str = "movie"
) -> List[Dict[str, Any]]:
    """
    Assigns a narration line to scenes, in this order of preference:
      1. hand-written lines from the essence database (narration_hi / narration_en),
      2. Gemini-written bridges for the remaining scenes (if an API key is given),
      3. localized templates.
    """
    if not scenes:
        return []

    for s in scenes:
        s["bridge_narration"] = pick_scene_narration(s, language)

    def needs_line(i: int) -> bool:
        s = scenes[i]
        if s.get("bridge_narration") or s.get("is_continuation"):
            return False
        if i == 0:
            return True
        prev = scenes[i - 1]
        return (s["start"] - prev["end"] > 30.0 or s.get("act") != prev.get("act")
                or content_mode == "lecture" or s.get("source") == "canonical_essence")

    missing = [i for i in range(len(scenes)) if needs_line(i)]

    if gemini_api_key and missing:
        try:
            from backend.video_engine.gemini_client import generate_json
            items = []
            for i in missing:
                s = scenes[i]
                items.append({"scene_index": i, "act": s.get("act", ""), "title": s.get("title", ""),
                              "what_happens": (s.get("reason") or "")[:300],
                              "dialogue_excerpt": (s.get("dialogue") or "")[:300]})
            kind = "lecture study cut" if content_mode == "lecture" else "movie recap"
            prompt = (f"You narrate a {kind} of '{movie_title}'. For each scene below, write ONE original sentence "
                      f"(10-20 words) in {language} that the narrator says as the scene begins. Use your own words; "
                      f"do not quote dialogue. Scene 0 should open the recap.\n\nScenes:\n{items}\n\n"
                      'Return JSON: {"bridges": [{"scene_index": 0, "narration": "..."}]}')
            parsed = generate_json(gemini_api_key, prompt, temperature=0.4, timeout=45)
            for b in parsed.get("bridges", []):
                idx = b.get("scene_index")
                if isinstance(idx, int) and 0 <= idx < len(scenes) and b.get("narration"):
                    scenes[idx]["bridge_narration"] = str(b["narration"]).strip()
        except Exception as e:
            print(f"Gemini narration failed, using templates: {e}", file=sys.stderr)

    templates = LOCALIZED_FALLBACKS.get(language, LOCALIZED_FALLBACKS["English"])
    t_idx = 0
    for i in missing:
        s = scenes[i]
        if s.get("bridge_narration"):
            continue
        if content_mode == "lecture" and i > 0:
            topic = s.get("topic") or s.get("title", "the next topic")
            s["bridge_narration"] = LECTURE_TEMPLATES.get(language, LECTURE_TEMPLATES["English"]).format(topic=topic)
        elif i == 0:
            s["bridge_narration"] = INTRO_TEMPLATES.get(language, INTRO_TEMPLATES["English"]).format(title=movie_title)
        else:
            s["bridge_narration"] = templates[t_idx % len(templates)]
            t_idx += 1
    return scenes


def _duck_expression(a: float, b: float, duck: float, ramp_in: float = 0.25, ramp_out: float = 0.45) -> str:
    """Volume envelope: full level, smooth dip to `duck` while the narrator speaks, smooth return."""
    k = 1.0 - duck
    a0 = max(0.0, a - ramp_in)
    span_in = max(0.01, a - a0)
    b1 = b + ramp_out
    return (f"if(lt(t,{a0:.3f}),1,"
            f"if(lt(t,{a:.3f}),1-{k:.3f}*(t-{a0:.3f})/{span_in:.3f},"
            f"if(lt(t,{b:.3f}),{duck:.3f},"
            f"if(lt(t,{b1:.3f}),{duck:.3f}+{k:.3f}*(t-{b:.3f})/{ramp_out:.3f},1))))")


def mix_voiceover_into_segment(video_segment_path: str, voiceover_audio_path: str, output_path: str,
                               lead_sec: float = VOICEOVER_LEAD_SEC, tempo: float = 1.0) -> bool:
    """
    Mixes narration into a clip with smooth ducking: the movie audio dips to VOICEOVER_DUCK_LEVEL while the
    narrator speaks and returns to full level afterwards. amix normalize=0 keeps the movie at its real level
    (the old version halved it), and a limiter prevents clipping.
    """
    from backend.video_engine.probe import get_media_duration
    vo_dur = (get_media_duration(str(voiceover_audio_path)) or 5.0) / max(1.0, tempo)
    a, b = lead_sec, lead_sec + vo_dur + 0.2
    delay_ms = int(round(lead_sec * 1000))
    tempo_filter = f"atempo={tempo:.3f}," if tempo > 1.001 else ""
    sr = AUDIO_SAMPLE_RATE
    filter_complex = (
        f"[0:a]aresample={sr},aformat=channel_layouts=stereo,"
        f"volume='{_duck_expression(a, b, VOICEOVER_DUCK_LEVEL)}':eval=frame[bg];"
        f"[1:a]aresample={sr},aformat=channel_layouts=stereo,{tempo_filter}"
        f"adelay={delay_ms}|{delay_ms},volume={VOICEOVER_GAIN}[vo];"
        f"[bg][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
    )
    cmd = [FFMPEG_BIN, "-y", "-i", str(video_segment_path), "-i", str(voiceover_audio_path),
           "-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k", "-ar", str(sr), "-ac", "2", str(output_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", timeout=180)
        if res.returncode == 0 and os.path.exists(output_path):
            return True
        print(f"Voiceover mix failed: {res.stderr.strip().splitlines()[-1:]}", file=sys.stderr)
    except Exception as e:
        print(f"Voiceover mix failed: {e}", file=sys.stderr)
    return False



def narrate_over_hold(video_segment_path: str, voiceover_audio_path: str, output_path: str, hold_sec: float) -> bool:
    """
    For clips that start with dialogue straight away: the narration is spoken over a still of the clip's first
    frame, then the clip plays with its own sound untouched, so the narrator never talks over anyone.
    """
    from backend.video_engine.probe import get_video_metadata, check_nvenc_support
    from backend.video_engine.renderer import video_codec_args, AUDIO_ARGS
    try:
        meta = get_video_metadata(video_segment_path)
    except Exception:
        meta = {}
    fps = float(meta.get("fps") or 25.0)
    frames = max(1, int(round(hold_sec * fps)))
    sr = AUDIO_SAMPLE_RATE
    fc = (f"[0:v]trim=end_frame=1,loop=loop={frames}:size=1:start=0,setpts=N/({fps:.3f}*TB),format=yuv420p,setsar=1[hv];"
          f"[1:a]aresample={sr},aformat=channel_layouts=stereo,volume={VOICEOVER_GAIN},apad=whole_dur={hold_sec:.3f},"
          f"atrim=0:{hold_sec:.3f}[ha];"
          f"[0:v]format=yuv420p,setsar=1[mv];[0:a]aresample={sr},aformat=channel_layouts=stereo[ma];"
          f"[hv][ha][mv][ma]concat=n=2:v=1:a=1[v][a]")
    for use_nvenc in ((True, False) if check_nvenc_support() else (False,)):
        cmd = [FFMPEG_BIN, "-y", "-i", str(video_segment_path), "-i", str(voiceover_audio_path), "-filter_complex", fc,
               "-map", "[v]", "-map", "[a]", "-r", f"{fps:.3f}", *video_codec_args(use_nvenc), *AUDIO_ARGS, str(output_path)]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 errors="replace", timeout=300)
            if res.returncode == 0 and os.path.exists(output_path):
                return True
            print(f"Narration hold failed: {res.stderr.strip().splitlines()[-1:]}", file=sys.stderr)
        except Exception as e:
            print(f"Narration hold failed: {e}", file=sys.stderr)
    return False



def synthesize_many(texts: List[str], voice_key: str, paths: List[str]) -> List[bool]:
    """Voices many lines at once. Gemini speech reads several lines per request (its free tier allows ~10 requests a
    day per model); other engines voice line by line. Returns which lines were produced by the chosen engine."""
    info = AVAILABLE_VOICES.get(voice_key, {})
    engine = info.get("engine")
    if engine not in ("gemini", "parler"):
        return [False] * len(texts)
    from backend.video_engine import tts_engines
    name = info["id"].split(":", 1)[1]
    done = (tts_engines.gemini_tts_batch(texts, name, paths, info["language"]) if engine == "gemini"
            else tts_engines.parler_tts_batch(texts, name, paths))
    for ok, p in zip(done, paths):
        if ok:
            _mark_engine(p, engine)
    return done
