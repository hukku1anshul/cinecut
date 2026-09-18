"""
Narration for the YouTube videos in voices that sound like people talking, not like a screen reader.

Each voice preset lists engines from the most natural to the most available. A video keeps one voice from start to end:
the whole script is voiced with the first engine that manages every part of it (if the free quota runs out halfway,
everything is voiced again with the next engine), so a viewer never hears the narrator change.

  hi      Hindi, male: Gemini speech told to speak pure Hindi like a Doordarshan narrator, native accent -> Edge Madhur
  hi-f    Hindi, female: Gemini -> Edge Swara
  (Indic Parler-TTS is not used for YouTube: its accent was judged poor and it misreads digits.)
  en-gb   English, British: Gemini told to sound like a documentary presenter from southern England -> Edge Ryan
  en-us   English, American: Gemini told to sound like a warm podcast host -> Edge Ava (Microsoft's conversational voice)

Gemini's free speech quota is small (a few requests a minute and a few dozen a day across the keys), so a whole
chapter or lesson part is voiced in one request, which also keeps the delivery continuous like a real take.
"""
import json
import re
import subprocess
import hashlib
import os
import shutil
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.config import FFMPEG_BIN
from backend.video_engine import narrator, tts_engines

STYLE = {
    "en-gb": ("Narrate this in a natural British accent (southern English), like a thoughtful documentary presenter talking to one "
              "listener: warm, unhurried, with natural pauses and a little wonder in the voice. Never sound like you are reading."),
    "en-us": ("Narrate this in a natural American accent, like a warm podcast host telling a friend something interesting: relaxed, "
              "conversational, with natural pauses. Never sound like you are reading."),
    "hi": ("इसे शुद्ध, स्पष्ट हिंदी में सुनाइए, जैसे दूरदर्शन या आकाशवाणी का कोई अनुभवी उद्घोषक किसी वृत्तचित्र का वाचन कर रहा हो: "
           "गरिमा के साथ, आत्मीयता से, बिना जल्दबाज़ी के, स्वाभाविक ठहराव के साथ। Speak as a native Hindi speaker from North India, with a "
           "natural Indian Hindi accent and correct Hindi pronunciation; never an English or foreign accent. Never sound like you are reading."),
    "hi-f": ("इसे शुद्ध, स्पष्ट हिंदी में सुनाइए, जैसे दूरदर्शन या आकाशवाणी की कोई अनुभवी उद्घोषिका वाचन कर रही हों: गरिमा और आत्मीयता के साथ, "
             "स्वाभाविक ठहराव के साथ। Speak as a native Hindi speaker from North India, with a natural Indian Hindi accent and correct "
             "Hindi pronunciation; never an English or foreign accent."),
}
TEACHER = {
    "en-gb": ("Say this like a friendly maths teacher from southern England explaining at the board to one student: natural British "
              "accent, relaxed and encouraging, clear with numbers, natural pauses. Never sound like you are reading. Say Sanskrit "
              "and Hindi words the way an Indian speaker says them, never anglicised."),
    "en-us": ("Say this like a friendly American maths teacher explaining at the board to one student: relaxed and encouraging, clear "
              "with numbers, natural pauses. Never sound like you are reading. Say Sanskrit "
              "and Hindi words the way an Indian speaker says them, never anglicised."),
    "hi": ("इसे ऐसे बोलिए जैसे कोई स्नेही गणित अध्यापक श्यामपट पर एक विद्यार्थी को समझा रहे हों: शुद्ध, स्पष्ट हिंदी, धैर्य से, उत्साह बढ़ाते हुए, "
           "अंक स्पष्ट, स्वाभाविक ठहराव के साथ। Speak as a native Hindi speaker from North India, with a natural Indian Hindi accent; "
           "never an English or foreign accent. Never sound like you are reading."),
    "hi-f": ("इसे ऐसे बोलिए जैसे कोई स्नेही गणित अध्यापिका श्यामपट पर एक विद्यार्थी को समझा रही हों: शुद्ध, स्पष्ट हिंदी, धैर्य से, अंक स्पष्ट। "
             "Speak as a native Hindi speaker from North India, with a natural Indian Hindi accent; never an English or foreign accent."),
}
# the Vedas course: a learned, warm teacher explaining to one curious listener
VEDA = {
    "en-gb": ("Say this like a friendly teacher explaining to one student: natural British accent, relaxed and encouraging, clear, "
              "with natural pauses, and careful pronunciation of Sanskrit words. Never sound like you are reading."),
    "en-us": ("Say this like a friendly teacher explaining to one student: relaxed and encouraging, clear, with natural pauses, and "
              "careful pronunciation of Sanskrit words. Never sound like you are reading."),
    "hi": ("इसे ऐसे बोलिए जैसे कोई स्नेही अध्यापक एक विद्यार्थी को समझा रहे हों: शुद्ध, स्पष्ट हिंदी, धैर्य से, उत्साह बढ़ाते हुए, "
           "स्वाभाविक ठहराव के साथ, और संस्कृत शब्दों का सही उच्चारण। Speak as a native Hindi speaker from North India, with a "
           "natural Indian Hindi accent; never an English or foreign accent. Never sound like you are reading."),
    "hi-f": ("इसे ऐसे बोलिए जैसे कोई स्नेही अध्यापिका एक विद्यार्थी को समझा रही हों: शुद्ध, स्पष्ट हिंदी, धैर्य से। Speak as a native "
             "Hindi speaker from North India, with a natural Indian Hindi accent."),
}
# a short story read aloud in full, the way a good audiobook narrator performs it
# the Vedas course: a teacher who is also a fine storyteller (the viewer: "make it interesting, so people enjoy listening")
COURSE = {
    "en-gb": ("Say this like a gifted British teacher who is also a wonderful storyteller, talking to one curious listener: warm and "
              "lively, drawing them in; slow down and lower the voice for a key moment, let a question hang for a beat before the "
              "answer, lift the energy when something is surprising, smile in the voice when it is light. Natural and personal, "
              "never theatrical, never like reading. Say Sanskrit and Hindi words the way an Indian speaker says them, never "
              "anglicised."),
    "en-us": ("Say this like a gifted American teacher who is also a wonderful storyteller, talking to one curious listener: warm and "
              "lively, drawing them in; slow down for a key moment, let a question hang for a beat before the answer, lift the "
              "energy when something is surprising. Natural, never theatrical, never like reading. Say Sanskrit and Hindi words "
              "the way an Indian speaker says them, never anglicised."),
    "hi": ("इसे ऐसे बोलिए जैसे कोई प्रिय अध्यापक, जो बहुत अच्छे कथावाचक भी हैं, एक जिज्ञासु श्रोता से बात कर रहे हों: आत्मीय और "
           "जीवंत, सुनने वाले को कथा में खींचते हुए; किसी महत्वपूर्ण क्षण पर गति धीमी और स्वर गहरा, प्रश्न के बाद उत्तर से पहले "
           "एक पल का ठहराव, कुछ चौंकाने वाली बात पर स्वर में उत्साह। स्वाभाविक, नाटकीय नहीं। Speak as a native Hindi speaker from "
           "North India, with a natural Indian Hindi accent; never an English or foreign accent. Never sound like you are reading."),
    "hi-f": ("इसे ऐसे बोलिए जैसे कोई प्रिय अध्यापिका, जो बहुत अच्छी कथावाचिका भी हैं, एक जिज्ञासु श्रोता से बात कर रही हों: आत्मीय, "
             "जीवंत, महत्वपूर्ण क्षण पर ठहराव के साथ। Speak as a native Hindi speaker from North India, with a natural Indian Hindi accent."),
}


def _styles(teacher) -> dict:
    """The style table for a teacher mode: True (lectures), "course" (the Vedas course), "story", "veda", or narration."""
    return (STORY if teacher == "story" else VEDA if teacher == "veda" else COURSE if teacher == "course"
            else TEACHER if teacher else STYLE)


STORY = {
    "en-gb": ("Read this story aloud like a master British audiobook narrator holding a listener spellbound: warm and "
              "intimate, living every moment; slow down and soften for tender or tense moments, quicken with excitement, let "
              "a pause land before a turn in the story. Give each character a distinct voice and manner, as a great narrator "
              "does, without overacting. Leave natural pauses between paragraphs. Never sound like you are reading."),
    "en-us": ("Read this short story aloud like a fine American audiobook narrator: warm, intimate and unhurried, following the "
              "feeling of each moment. Give the characters slightly different voices without overacting. Never sound like you are reading."),
    "hi": ("इस कहानी को ऐसे सुनाइए जैसे कोई कुशल कथावाचक श्रोताओं को बाँधे रखता है: आत्मीयता से, हर क्षण को जीते हुए; कोमल या "
           "तनाव भरे क्षणों में धीमे और धीरे, उत्साह के क्षणों में तेज़, कहानी के मोड़ से पहले एक ठहराव; हर पात्र का अपना अलग "
           "स्वर और ढंग, पर अतिरेक के बिना; अनुच्छेदों के बीच स्वाभाविक ठहराव। Speak as a native Hindi "
           "speaker from North India, with a natural Indian Hindi accent and correct Hindi pronunciation; never an English or foreign "
           "accent. Never sound like you are reading."),
    "hi-f": ("इस कहानी को ऐसे सुनाइए जैसे कोई अनुभवी कथावाचिका आकाशवाणी पर कहानी पढ़ रही हों: आत्मीयता से, हर क्षण के भाव के साथ; "
             "पात्रों की बातें थोड़े अलग स्वर में। Speak as a native Hindi speaker from North India, with a natural Indian Hindi accent."),
}
PRESETS: Dict[str, Dict] = {
    "hi": {"label": "Hindi (male)", "language": "Hindi", "gemini": "Iapetus", "edge": "hi_madhur"},
    "hi-f": {"label": "Hindi (female)", "language": "Hindi", "gemini": "Sulafat", "edge": "hi_swara"},
    "en-gb": {"label": "English, British (male)", "language": "English", "gemini": "Algieba", "edge": "en_gb_ryan"},
    "en-us": {"label": "English, American (female)", "language": "English", "gemini": "Sulafat", "edge": "en_us_ava"},
}
DEFAULT = {"hi": "hi", "en": "en-gb"}
narrator.AVAILABLE_VOICES.setdefault("en_gb_ryan", {"id": "en-GB-RyanNeural", "name": "Ryan (British English)", "language": "English", "lang_code": "en"})
narrator.AVAILABLE_VOICES.setdefault("en_us_ava", {"id": "en-US-AvaMultilingualNeural", "name": "Ava (American English, conversational)",
                                                   "language": "English", "lang_code": "en"})
narrator.AVAILABLE_VOICES.setdefault("en_us_andrew", {"id": "en-US-AndrewMultilingualNeural", "name": "Andrew (American English, conversational)",
                                                      "language": "English", "lang_code": "en"})


# The cast: each series has its own narrator, and an elder voice for the words of the work itself (Gemini voice names).
# The Vedas narrators are the two voices the viewer approved.
CAST = {
    "vedas": {"hi": ("Achird", None), "en": ("Sadaltager", None)},       # the lecture teachers (the viewer preferred them)
    "audio": {"hi": ("Achird", None), "en": ("Sadaltager", None)},
    "classics": {"hi": ("Sulafat", None), "en": ("Achernar", None)},
    "lectures": {"hi": ("Achird", None), "en": ("Sadaltager", None)},
    "maths": {"hi": ("Iapetus", None), "en": ("Algieba", None)},
}
FEMALE_VOICES = {"Gacrux", "Sulafat", "Achernar", "Kore", "Leda", "Aoede", "Zephyr", "Callirrhoe", "Autonoe", "Despina", "Erinome",
                 "Laomedeia", "Pulcherrima", "Vindemiatrix"}
# hymns whose seer is a woman: their words are read by an elder woman's voice (Gacrux) instead
FEMALE_SEERS = ("वागाम्भृणी", "घोषा", "लोपामुद्रा", "शची", "अपाला", "इन्द्राणी", "सूर्या", "यमी", "उर्वशी", "विश्ववारा", "रोमशा", "गोधा",
                "सरमा", "श्रद्धा", "दक्षिणा", "जुहू", "अदिति")
SANSKRIT_STYLE = ("Recite this Vedic Sanskrit mantra the way a learned pandit from North India recites it at the fire altar: "
                  "slowly and reverently, in the Hindi-speaking tradition of Sanskrit pronunciation (as heard in Varanasi or "
                  "Haridwar), every syllable distinct, a short pause at each danda. A native Hindi speaker's voice and accent; "
                  "never an English or foreign accent.")
RECITER = "Achird"      # every Sanskrit verse, in Hindi and English videos alike: the Hindi teacher's voice


def poet_style(language: str, voice: str) -> str:
    elder = "an old woman" if voice in FEMALE_VOICES else "an old man"
    if language == "Hindi":
        return (f"the voice of {elder}, slow and reverent, like a sage reciting ancient words; pure Hindi, spoken as a native "
                "Hindi speaker from North India, never an English or foreign accent")
    return f"the voice of {elder}, slow and reverent, like an elder reading ancient words aloud; a natural British accent"


def cast_for(series: str, code: str, preset: str, rishi: str = "") -> Tuple[str, Optional[str]]:
    """(narrator voice, poet voice or None) for a video. A voice the user picked in place of the default keeps its narrator."""
    narrator, poet = CAST.get(series, {}).get(code, (None, None))
    if preset != DEFAULT.get(code) or not narrator:
        narrator = PRESETS[preset]["gemini"]
    if poet and any(n in (rishi or "") for n in FEMALE_SEERS):
        poet = "Gacrux"
    return narrator, poet


def recite_sanskrit(lines: List[str], voice: str, cache: Path, work: Path) -> Optional[Tuple[Path, float]]:
    """The first mantra of a hymn recited in Sanskrit by the poet's voice (kept in `cache`). Returns (wav, seconds) or None."""
    text = re.sub(r"\s*॥\s*[०-९\d]+\s*॥", " ॥", "\n".join(lines)).strip()
    if not cache.exists():
        if not tts_engines.gemini_tts(text, voice, str(cache), "Hindi", timeout=120, style=SANSKRIT_STYLE):
            return None
    wav = work / "mantra.wav"
    dur = to_wav(cache, wav)
    if not 2.5 <= dur <= 45:
        cache.unlink(missing_ok=True)
        return None
    return wav, dur


QUOTE_RE = re.compile(r"[“\"]([^”\"]{12,}?)[”\"]|(?<=[\s:,(])'([^']{12,}?)'(?=[\s.,;:!?)]|$)")


def turns(text: str, whole: Optional[str] = None) -> List[Tuple[str, str]]:
    """A part as speaker turns: quotations of four words or more go to the Poet, the rest to the Narrator. `whole` gives the
    whole part to one speaker (a verse read by the Poet)."""
    if whole:
        return [(whole, text.strip())]
    out: List[Tuple[str, str]] = []
    pos = 0
    for m in QUOTE_RE.finditer(text):
        q = (m.group(1) or m.group(2) or "").strip()
        lead = text[:m.start()].rstrip()[-1:]
        if len(q.split()) < 4 or (lead and lead not in ":,.!?।—–-”\"'’"):
            continue
        before = text[pos:m.start()].strip()
        if before:
            out.append(("Narrator", before))
        out.append(("Poet", q))
        pos = m.end()
    rest = text[pos:].strip(" \n")
    if rest:
        out.append(("Narrator", rest))
    return out or [("Narrator", text.strip())]


NATURAL_ONLY = False      # batch jobs set this: no standard voice ever, they wait for Gemini's quota instead


def engines(preset: str) -> List[str]:
    p = PRESETS[preset]
    out = []
    if tts_engines.gemini_available():
        out += [f"gemini:{m}" for m in tts_engines.GEMINI_TTS_MODELS]
    if NATURAL_ONLY:
        return out
    if p.get("parler") and tts_engines.parler_available():
        out.append("parler")
    return out + ["edge"]


def _split_sentences(text: str, max_chars: int = 220) -> List[str]:
    out: List[str] = []
    for s in re.split(r"(?<=[.!?।])\s+", text.strip()):
        while len(s) > max_chars:
            cut = s.rfind(",", 0, max_chars)
            cut = cut if cut > 40 else max_chars
            out.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            out.append(s)
    return out


HI_0_99 = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
           "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस "
           "उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरपन चौवन "
           "पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर "
           "तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी "
           "नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे").split()
assert len(HI_0_99) == 100


def hindi_number(n: int) -> str:
    """A whole number in Hindi words, e.g. 1710 -> 'एक हज़ार सात सौ दस' (lakh and crore above a hundred thousand)."""
    if n < 100:
        return HI_0_99[n]
    for size, word in ((10 ** 7, "करोड़"), (10 ** 5, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= size:
            head, rest = divmod(n, size)
            return f"{hindi_number(head)} {word}" + (f" {hindi_number(rest)}" if rest else "")
    return str(n)


def hindi_digits(text: str) -> str:
    """Numbers written as Hindi words before speaking: some voices misread digits inside Hindi ('39' heard as '19')."""
    text = re.sub(r"(\d)[,](\d)", r"\1\2", text)
    return re.sub(r"\d+", lambda m: hindi_number(int(m.group(0))) if len(m.group(0)) <= 9 else m.group(0), text)


EN_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
           "eighteen nineteen").split()
EN_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def english_number(n: int) -> str:
    """A whole number in British English words, e.g. 8924 -> 'eight thousand, nine hundred and twenty-four'."""
    if n < 20:
        return EN_ONES[n]
    if n < 100:
        return EN_TENS[n // 10] + (f"-{EN_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{EN_ONES[n // 100]} hundred" + (f" and {english_number(n % 100)}" if n % 100 else "")
    if n < 1_000_000:
        head, rest = divmod(n, 1000)
        return f"{english_number(head)} thousand" + ((" and " if rest < 100 else ", ") + english_number(rest) if rest else "")
    return str(n)


def english_big_numbers(text: str) -> str:
    """Numbers of four digits or more written as words for the maths teacher: a voice read the answer 8924 as
    "8, 9, 2, 4". Only for lessons: in documentaries a four-digit number is usually a year."""
    return re.sub(r"\b\d{4,6}\b", lambda m: english_number(int(m.group(0))), text)


def _speak(engine: str, text: str, preset: str, out: Path, teacher: bool, work: Path,
           cast: Optional[Tuple[str, Optional[str]]] = None) -> bool:
    p = PRESETS[preset]
    if p["language"] == "Hindi" and engine != "edge":
        text = hindi_digits(text)
    if p["language"] == "English" and teacher:
        text = english_big_numbers(text)
    if engine.startswith("gemini:"):
        style = _styles(teacher)[preset] + " Leave a clear pause between paragraphs."
        narrator = (cast or (None, None))[0] or p["gemini"]
        poet = (cast or (None, None))[1]
        if poet and re.search(r"^Poet:", text, re.M):
            style = (f"Read this aloud with two voices. Make Narrator sound like this: {style} "
                     f"Make Poet sound like this: {poet_style(p['language'], poet)}.")
            return tts_engines.gemini_tts(text, narrator, str(out), p["language"], timeout=180, style=style, models=(engine[7:],),
                                          speakers=[("Narrator", narrator), ("Poet", poet)])
        text = re.sub(r"^(?:Narrator|Poet): ", "", text, flags=re.M)
        return tts_engines.gemini_tts(text, narrator, str(out), p["language"], timeout=180, style=style, models=(engine[7:],))
    if engine == "parler":
        parts = _split_sentences(text)
        paths = [str(work / f"{out.stem}_p{i}.mp3") for i in range(len(parts))]
        try:
            res = tts_engines.parler_tts_batch(parts, p["parler"], paths)
        except Exception:
            return False
        if not res or not all(Path(x).exists() and Path(x).stat().st_size > 200 for x in paths):
            return False
        return _concat(paths, out, gap=0.28)
    narrator.synthesize_voiceover(text, p["edge"], str(out), rate="-4%")
    return out.exists() and out.stat().st_size > 200


def _concat(paths: List[str], out: Path, gap: float) -> bool:
    inputs, filt = [], []
    for i, pth in enumerate(paths):
        inputs += ["-i", pth]
        filt.append(f"[{i}:a]aresample=24000,aformat=channel_layouts=mono,apad=pad_dur={gap}[a{i}]")
    graph = ";".join(filt) + ";" + "".join(f"[a{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=0:a=1[o]"
    r = subprocess.run([FFMPEG_BIN, "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-c:a", "libmp3lame", "-b:a", "128k", str(out)],
                       capture_output=True)
    return r.returncode == 0 and out.exists()


def _groups(texts: List[str], limit: int) -> List[List[int]]:
    """Consecutive parts packed into takes of at most `limit` characters (a longer part is a take of its own)."""
    out: List[List[int]] = []
    size = 0
    for i, t in enumerate(texts):
        if out and size + len(t) <= limit:
            out[-1].append(i)
            size += len(t)
        else:
            out.append([i])
            size = len(t)
    return out


def _cut(wav: Path, starts: List[float], duration: float, work: Path, stem: str) -> List[Path]:
    """Cuts one take into parts at the given start times."""
    with wave.open(str(wav), "rb") as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    bounds = starts + [duration]
    out = []
    for k in range(len(starts)):
        a, b = int(bounds[k] * rate) * width * ch, int(bounds[k + 1] * rate) * width * ch
        path = work / f"{stem}_{k}.wav"
        with wave.open(str(path), "wb") as o:
            o.setnchannels(ch)
            o.setsampwidth(width)
            o.setframerate(rate)
            o.writeframes(frames[a:b])
        out.append(path)
    return out


def take_complete(wav: Path, last_text: str, language: str, work: Path) -> bool:
    """Whether a take reached its last line. A voice sometimes stops early (one lesson take ended after "have a go", so the
    practice answer and the goodbye came out silent). The take's last 25 seconds are transcribed on their own (Whisper is
    reliable on short clips) and at least half of the final sentence's words must be heard. Trusted without a Groq key."""
    from backend.video_engine import speech_transcriber as st
    if not last_text.strip() or not st.groq_transcription_available():
        return True
    with wave.open(str(wav), "rb") as w:
        dur = w.getnframes() / float(w.getframerate())
    tail = work / f"{wav.stem}_tail.wav"
    subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-ss", f"{max(0.0, dur - 25):.2f}", "-i", str(wav), str(tail)], check=True)
    try:
        ok, subs, _ = st.transcribe_with_groq(str(tail), str(work / f"{wav.stem}_tail.srt"), language="hi" if language == "Hindi" else "en")
    except Exception:
        return True
    if not ok:
        return True
    heard = set(re.findall(r"\w+", " ".join(x["text"] for x in subs).lower()))
    want = [w for w in re.findall(r"\w+", (hindi_digits(last_text) if language == "Hindi" else last_text).lower())
            if len(w) > 2 and not w.isdigit()]
    if len(want) < 2:
        return True
    return sum(1 for w in want if w in heard) >= 0.5 * len(want)


def _labelled(text: str, cast: Optional[Tuple[str, Optional[str]]], whole: Optional[str]) -> str:
    """A part as "Narrator: ..." / "Poet: ..." turns when the cast has a second voice."""
    if not (cast and cast[1]):
        return text
    return "\n".join(f"{who}: {' '.join(x.split())}" for who, x in turns(text, whole))


def _gemini_takes(texts: List[str], engine: str, preset: str, work: Path, teacher: bool, limit: int,
                  progress: Optional[Callable[[str], None]], cast: Optional[Tuple[str, Optional[str]]] = None,
                  whole: Optional[List[Optional[str]]] = None) -> Optional[List[Path]]:
    """Several parts read in one continuous take (one request), then cut apart where each part begins: a natural single
    performance, and far fewer requests from the small free quota. A take that stops before its last line is asked for
    again, then split into two smaller takes."""
    files: List[Optional[Path]] = [None] * len(texts)
    language = PRESETS[preset]["language"]
    counter = [0]

    def take(idx: List[int], tries_left: int = 1) -> bool:
        counter[0] += 1
        g = counter[0]
        if progress:
            progress(f"voicing take {g} (gemini)")
        take_text = "\n\n".join(_labelled(texts[i], cast, whole[i] if whole else None) for i in idx)
        kept = VOICE_CACHE / "takes" / (hashlib.sha1(json.dumps([take_text, engine, preset, str(teacher), cast, POET_STYLE_VERSION],
                                                                ensure_ascii=False).encode("utf-8")).hexdigest()[:20] + ".wav")
        wav = work / f"take{g}.wav"
        if kept.exists():                                     # read before, and checked complete: a retry after the quota ran out
            dur = to_wav(kept, wav)
        else:
            mp3 = work / f"take{g}_{re.sub(r'[^a-z0-9]', '', engine)}.mp3"
            try:
                ok = _speak(engine, take_text, preset, mp3, teacher, work, cast)
            except Exception:
                ok = False
            if not ok:
                return False
            dur = to_wav(mp3, wav)
            last = (_split_sentences(texts[idx[-1]]) or [""])[-1]
            if not take_complete(wav, last, language, work):
                if tries_left > 0:
                    return take(idx, tries_left - 1)          # the same take once more
                if len(idx) > 1:                              # then in two smaller takes
                    half = len(idx) // 2
                    return take(idx[:half], 1) and take(idx[half:], 1)
                return False
            try:
                kept.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(wav, kept)
            except OSError:
                pass
        if len(idx) == 1:
            files[idx[0]] = wav
            return True
        cuts = starts(wav, [texts[i] for i in idx], dur, language, work)
        for i, part in zip(idx, _cut(wav, cuts, dur, work, f"take{g}")):
            files[i] = part
        return True

    for idx in _groups(texts, limit):
        if not take(idx):
            return None
    return [f for f in files if f is not None] if all(files) else None


POET_STYLE_VERSION = 1        # bump when the reading styles change, so old takes are not reused
VOICE_CACHE = Path(__file__).resolve().parents[2] / "output" / "youtube" / "_voices"
VOICE_CACHE_MB = 4000


def _cache_key(texts: List[str], preset: str, teacher: Any, cast: Any, whole: Any) -> str:
    style = _styles(teacher).get(preset, "")
    blob = json.dumps([texts, preset, str(teacher), style, cast, whole, POET_STYLE_VERSION], ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _from_cache(key: str, n: int, work: Path) -> Optional[Tuple[List[Path], str]]:
    d = VOICE_CACHE / key
    meta = d / "engine.txt"
    if not meta.exists():
        return None
    files = [d / f"part{i}.wav" for i in range(n)]
    if not all(f.exists() for f in files):
        return None
    out = []
    for i, f in enumerate(files):
        dst = work / f"cached{i}.wav"
        shutil.copy(f, dst)
        out.append(dst)
    os.utime(d)
    return out, meta.read_text(encoding="utf-8").strip()


def _to_cache(key: str, files: List[Path], engine: str) -> None:
    if not engine.startswith("gemini"):
        return                                        # only natural voices are worth keeping
    d = VOICE_CACHE / key
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(files):
        to_wav(f, d / f"part{i}.wav")
    (d / "engine.txt").write_text(engine, encoding="utf-8")
    dirs = sorted((x for x in VOICE_CACHE.iterdir() if x.is_dir()), key=lambda x: x.stat().st_mtime)
    total = sum(f.stat().st_size for x in dirs for f in x.glob("*"))
    while dirs and total > VOICE_CACHE_MB * 1024 * 1024:
        old = dirs.pop(0)
        total -= sum(f.stat().st_size for f in old.glob("*"))
        shutil.rmtree(old, ignore_errors=True)


def voice_parts(texts: List[str], preset: str, work: Path, teacher: bool = False,
                progress: Optional[Callable[[str], None]] = None, take_chars: int = 2600,
                cast: Optional[Tuple[str, Optional[str]]] = None, whole: Optional[List[Optional[str]]] = None) -> Tuple[List[Path], str]:
    """One audio file per text. Returns (files, engine used). With a `cast` (narrator, poet), Gemini reads in the narrator's
    voice and gives quotations to the poet (or whole parts, per `whole`: "Poet" for a verse, "Narrator" for the rest).
    Natural-voice results are kept (VOICE_CACHE), so the same script read again costs no quota."""
    work.mkdir(parents=True, exist_ok=True)
    key = _cache_key(texts, preset, teacher, cast, whole)
    hit = _from_cache(key, len(texts), work)
    if hit:
        if progress:
            progress("voice from the cache")
        return hit
    for engine in engines(preset):
        if engine.startswith("gemini:"):
            got = _gemini_takes(texts, engine, preset, work, teacher, take_chars, progress, cast, whole)
            if got:
                try:
                    _to_cache(key, got, engine)
                except Exception:
                    pass
                return got, engine
            continue
        files: List[Path] = []
        for i, t in enumerate(texts):
            if progress:
                progress(f"voicing part {i + 1} of {len(texts)} ({engine.split(':')[0]})")
            out = work / f"part{i}_{re.sub(r'[^a-z0-9]', '', engine)}.mp3"
            try:
                ok = _speak(engine, t, preset, out, teacher, work)
            except Exception:
                ok = False
            if not ok:
                break
            files.append(out)
        if len(files) == len(texts):
            return files, engine
    raise RuntimeError("no voice could read the script")


def to_wav(src: Path, dst: Path) -> float:
    """Converts to 24 kHz mono WAV with the silence trimmed from both ends; returns its length in seconds."""
    af = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse")
    subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", "24000", "-ac", "1", str(dst)], check=True)
    with wave.open(str(dst), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def silences(wav: Path, min_len: float = 0.22, noise_db: int = -40) -> List[Tuple[float, float]]:
    r = subprocess.run([FFMPEG_BIN, "-v", "info", "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def line_starts(wav: Path, lines: List[str], duration: float) -> List[float]:
    """When each line starts inside one take that reads several lines in a row. Each boundary is first estimated from the
    line's share of the text still to come (from the boundary before it, so errors do not add up), then moved to the best
    pause near that estimate: the breaks between lines are the longest pauses, so longer pauses win over closer ones."""
    gaps = silences(wav)
    out, last = [0.0], 0.0
    for k in range(len(lines) - 1):
        remaining = sum(len(x) for x in lines[k:]) or 1
        guess = last + (duration - last) * len(lines[k]) / remaining
        near = [((b - a) - 0.3 * abs((a + b) / 2 - guess), b) for a, b in gaps
                if b > last + 0.4 and abs((a + b) / 2 - guess) < 1.8]
        t = max(near)[1] - 0.05 if near else guess
        t = max(t, last + 0.4)
        out.append(t)
        last = t
    return out


def align_starts(wav: Path, lines: List[str], language: str, work: Path) -> Optional[List[float]]:
    """Where each line starts, from word timings of a transcription of the take itself (Groq Whisper). The script is lined
    up with the transcript letter by letter (so small spelling differences do not matter) and each line is cut in the gap
    before its first word. None when there is no Groq key or the transcript does not match the script well enough."""
    from difflib import SequenceMatcher
    from backend.video_engine import speech_transcriber as st
    if len(lines) < 2 or not st.groq_transcription_available():
        return None
    srt = work / f"{wav.stem}_align.srt"
    try:
        ok, _, _ = st.transcribe_with_groq(str(wav), str(srt), language="hi" if language == "Hindi" else "en")
    except Exception:
        return None
    wj = srt.with_suffix(".words.json")
    if not ok or not wj.exists():
        return None
    words = json.loads(wj.read_text(encoding="utf-8"))

    def norm(s: str) -> str:
        return re.sub(r"[\W_]+", "", s.lower())

    heard, spans = "", []
    for w in words:
        t = norm(w["word"])
        spans.append((len(heard), len(heard) + len(t)))
        heard += t
    script, offsets = "", []
    for ln in lines:
        offsets.append(len(script))
        script += norm(hindi_digits(ln) if language == "Hindi" else ln)
    if not heard or not script:
        return None
    blocks = [b for b in SequenceMatcher(None, script, heard, autojunk=False).get_matching_blocks() if b.size]
    if sum(b.size for b in blocks) < 0.55 * len(script):
        return None
    out = [0.0]
    for off in offsets[1:]:
        blk = next((b for b in blocks if b.a + b.size > off), None)
        if blk is None:
            return None
        at = blk.b + max(0, off - blk.a)
        idx = next((i for i, (_, e) in enumerate(spans) if e > at), len(spans) - 1)
        start = words[idx]["start"]
        prev_end = words[idx - 1]["end"] if idx else 0.0
        cut = (prev_end + start) / 2 if 0 <= start - prev_end < 1.2 else start - 0.12
        out.append(max(cut, out[-1] + 0.3))
    return out


def starts(wav: Path, lines: List[str], duration: float, language: str, work: Path) -> List[float]:
    """Line starts inside one take: word-level alignment when possible, else the pauses."""
    return align_starts(wav, lines, language, work) or line_starts(wav, lines, duration)
