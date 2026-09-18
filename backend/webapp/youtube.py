"""
YouTube packs in Hindi and English: a finished video, its thumbnail and everything to paste into YouTube Studio (title,
description with credits and chapters, tags, a transcript for subtitles), waiting for your approval.

Series
  classics  books and stories retold as short documentaries
  vedas     the Rigveda hymns and the Upanishads, from public-domain translations
  maths     maths lessons as Shorts (rule, three worked examples, a practice problem), every example checked by code
Each video is made in Hindi, English or both; each language is its own video.

Long videos are made like documentaries, not slide decks: a script written the way a narrator talks (storyteller.py),
a natural voice that stays the same from start to end (yt_voice.py), a cold open over a painting, a title card, chapter
cards and on-screen quotations timed to when they are spoken, slow pans and dissolves across public-domain paintings from
Wikimedia Commons (credited in the description), light film grain and levelled sound.

Rules: YouTube shows a video everywhere, so only titles free in India, the US and the UK are used; never film clips
(Content ID claims even public-domain films); never NPTEL lectures (their share-alike licence does not fit YouTube's
licence options). Nothing is uploaded from here: you publish from YouTube Studio and mark the pack published.
(Videos uploaded through the YouTube API stay private until Google audits the app, so publishing by hand comes first.)
"""
import json
import re
import secrets
import shutil
import subprocess
import threading
import time
import traceback
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import FFMPEG_BIN, OUTPUT_DIR, TEMP_DIR
from backend.video_engine import endcard
from backend.webapp import artwork, lessons, library as lib, music, sanskrit, storyteller, yt_voice

YT_DIR = OUTPUT_DIR / "youtube"
LANGS = {"hi": "Hindi", "en": "English"}
SERIES = {"classics": "हिंदी की अमर कृतियाँ", "vedas": "वेद और उपनिषद", "maths": "गणित की त्वरित विधियाँ",
          "lectures": "व्याख्यान, सरल शब्दों में", "audio": "सुनिए: ऋग्वेद"}
SERIES_EN = {"classics": "Classics", "vedas": "Vedas & Upanishads", "maths": "Maths Shortcuts",
             "lectures": "Lectures in Brief", "audio": "Listen: the Rigveda"}
STATUSES = ("draft", "approved", "published", "rejected")
STATE: Dict[str, Any] = {"running": False, "series": None, "language": None, "done": 0, "wanted": 0, "message": "", "errors": [], "asked": []}
_lock = threading.Lock()
WORDS = {
    "hi": {"opening": "शुरुआत", "original": "मूल रचना", "rights": "अधिकार", "source": "स्रोत", "paintings": "चित्र", "text": "मूल पाठ",
           "chapter": "अध्याय {n}", "chapters": "अध्याय",
           "vedas_note": "यह अंग्रेज़ी अनुवाद पर आधारित है; अनुवादक का नाम ऊपर है। परंपरागत और विद्वानों की व्याख्याएँ कई जगह अलग होती हैं।",
           "ai_note": "पटकथा और वाचन CineCut ने AI उपकरणों की सहायता से, ऊपर दिए सार्वजनिक (public-domain) स्रोतों के आधार पर तैयार किए हैं।",
           "tags": ["हिंदी", "हिंदी साहित्य", "CineCut"], "hashtags": {"vedas": "#वेद #ऋग्वेद #उपनिषद", "classics": "#हिंदीसाहित्य #कथा #उपन्यास",
                                                          "lectures": "#शिक्षा #व्याख्यान #पढ़ाई", "audio": "#ऋग्वेद #वेद #सूक्त"},
           "vedas_tags": ["वेद", "ऋग्वेद", "उपनिषद", "Rigveda in Hindi", "Upanishad in Hindi"]},
    "en": {"opening": "Opening", "original": "Original work", "rights": "Rights", "source": "Source", "paintings": "Paintings", "text": "Original text",
           "chapter": "Chapter {n}", "chapters": "Chapters",
           "vedas_note": "Based on a public-domain English translation (translator named above); traditional and scholarly readings differ in places.",
           "ai_note": "Script and narration produced with AI tools by CineCut, from the public-domain sources above.",
           "tags": ["documentary", "history", "CineCut"], "hashtags": {"vedas": "#Rigveda #Vedas #Upanishads", "classics": "#Classics #Literature #Storytelling",
                                                          "lectures": "#Education #Lecture #Learning", "audio": "#Rigveda #Vedas #Hymns"},
           "vedas_tags": ["Vedas", "Rigveda", "Upanishads", "Hindu scriptures", "ancient India"]},
}


# ------------------------------------------------------------------ the queue
def pack_dir(pid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_]{6,40}", pid or ""):
        raise KeyError(pid)
    return YT_DIR / pid


def queue() -> List[Dict[str, Any]]:
    out = []
    if YT_DIR.exists():
        for d in YT_DIR.iterdir():
            p = d / "pack.json"
            if p.exists():
                try:
                    out.append(json.loads(p.read_text(encoding="utf-8")))
                except ValueError:
                    continue
    return sorted(out, key=lambda x: -x.get("created", 0))


def _save(pack: Dict[str, Any]) -> Dict[str, Any]:
    (pack_dir(pack["id"]) / "pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    return pack


def set_status(pid: str, status: str, url: Optional[str] = None) -> Dict[str, Any]:
    if status not in STATUSES:
        raise ValueError("Unknown status.")
    pack = json.loads((pack_dir(pid) / "pack.json").read_text(encoding="utf-8"))
    pack.update(status=status, updated=time.time())
    if url:
        if not re.match(r"https://(www\.)?(youtube\.com|youtu\.be)/", url):
            raise ValueError("Paste the YouTube link of the published video.")
        pack["youtube_url"] = url
    return _save(pack)


def metadata_text(pack: Dict[str, Any]) -> str:
    lang = "Hindi" if pack.get("language", "hi") == "hi" else "English"
    synthetic = ("Yes (the narrator is a realistic AI voice)" if pack.get("altered_or_synthetic")
                 else "No (a standard synthetic narration voice, no real person or event is depicted)")
    subs = ("\n\nSUBTITLES\nIn YouTube Studio: Subtitles > Add language > Upload file > Without timing, and choose transcript.txt "
            "(YouTube times it to the voice)." if (pack.get("files") or {}).get("transcript") else "")
    return (f"TITLE\n{pack['title']}\n\nDESCRIPTION\n{pack['description']}\n\nTAGS\n{', '.join(pack.get('tags') or [])}\n\n"
            f"SETTINGS\nCategory: Education | Language: {lang} | Made for kids: No | Altered or synthetic content: {synthetic}{subs}\n")


# ------------------------------------------------------------------ choosing what to make
def _cleared_everywhere(e: Dict[str, Any]) -> bool:
    t = lib.territories(e)
    return "ALL" in t or {"IN", "US", "GB"} <= set(t)


def script_for(e: Dict[str, Any], language: str) -> Optional[Dict[str, Any]]:
    sc = (e.get("scripts") or {}).get(language)
    if sc:
        return sc
    return e.get("script") if lib.primary_language(e) == language else None


def _fits(e: Dict[str, Any], series: str) -> bool:
    b = e.get("build") or {}
    if series == "lectures":                        # Creative Commons lectures that allow commercial use; never NPTEL (CC BY-SA)
        lic = ((e.get("rights") or {}).get("license") or "").lower()
        return (e.get("kind") == "lecture" and e.get("visibility", "public") == "public" and _cleared_everywhere(e)
                and "nptel" not in (e.get("creator") or "").lower() and not re.search(r"\bnc\b|non-?commercial|-sa\b|sharealike", lic))
    if series == "audio":                           # the Rigveda hymns: short, and their translation is public domain
        return _fits(e, "vedas") and "ऋग्वेद" in e.get("title", "")
    if e.get("kind") not in ("book", "story") or e.get("visibility", "public") != "public" or not _cleared_everywhere(e):
        return False                                # explainers only: no film recaps
    if "nptel" in (e.get("creator") or "").lower():
        return False
    is_veda = b.get("yt_series") == "vedas"
    if (series == "vedas") != is_veda:
        return False
    return not (series == "classics" and (b.get("govuk") or b.get("nasa_srt") or b.get("wikisource_api")))


def candidates(series: str, n: int, code: str = "hi") -> List[Dict[str, Any]]:
    """Ready titles with a summary in this language that have no pack in it yet."""
    lang = LANGS[code]
    done = {(p.get("entry_id"), p.get("language", "hi")) for p in queue() if p.get("status") != "rejected"}
    out = [e for e in lib.all_entries() if e.get("status") == "ready" and (e["id"], code) not in done and _fits(e, series)
           and lang in (e.get("languages") or []) and script_for(e, lang)]
    return sorted(out, key=lambda e: -(e.get("priority") or 0))[:n]


def untranslated(series: str, n: int) -> List[Dict[str, Any]]:
    """Ready titles with a Hindi summary but no English one yet (translated before filming in English)."""
    out = [e for e in lib.all_entries() if e.get("status") == "ready" and _fits(e, series) and script_for(e, "Hindi")
           and not script_for(e, "English")]
    return sorted(out, key=lambda e: -(e.get("priority") or 0))[:n]


def waiting(series: str, n: int) -> List[Dict[str, Any]]:
    """Titles that would qualify once their summary is made (the builder is asked to make these first)."""
    out = []
    for e in lib.all_entries():
        b = e.get("build") or {}
        if e.get("status") != "catalog" or not _fits(e, series) or b.get("state") == "skipped":
            continue
        if series == "classics" and not b.get("wikisource") and not b.get("gutenberg_id"):
            continue
        out.append(e)
    return sorted(out, key=lambda e: -(e.get("priority") or 0))[:n]


# ------------------------------------------------------------------ pictures
def _run(cmd: List[str], cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(((r.stderr or "").strip().splitlines() or ["ffmpeg failed"])[-1][:200])


MOVES = [  # (zoom, x, y) as zoompan expressions of p = progress 0..1 through the shot
    ("1+0.13*p", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),                  # slow push in
    ("1.13-0.13*p", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),               # slow pull out
    ("1.12", "(iw-iw/zoom)*p", "ih/2-(ih/zoom/2)"),                        # pan left to right
    ("1.12", "(iw-iw/zoom)*(1-p)", "ih/2-(ih/zoom/2)"),                    # pan right to left
    ("1.08+0.06*p", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*(0.2+0.6*p)"),       # drift down while pushing in
]


def _shots(bounds: List[float], longest: float = 26.0) -> List[float]:
    """Shot lengths: each chapter is split into shots of at most `longest` seconds."""
    out = []
    for a, b in zip(bounds, bounds[1:]):
        n = max(1, int((b - a) // longest) + (1 if (b - a) % longest > 4 else 0))
        out += [(b - a) / n] * n
    return out


def _background(series: str, hint: str, bounds: List[float], work: Path,
                subjects: Optional[List[str]] = None, avoid: Optional[str] = None) -> Tuple[Path, List[str], Optional[Path]]:
    """A 1920x1080 moving background for the whole video: public-domain paintings, one move per shot, a new shot at every
    chapter and every ~26 seconds, joined with one-second dissolves. A moving gradient when no painting is found.
    Returns (path, painting credits, first painting)."""
    out, total = work / "bg.mp4", bounds[-1]
    shots = _shots(bounds)
    images = []
    try:
        for img in artwork.pick(series, hint, min(12, len(shots)), subjects, avoid):
            try:
                images.append((artwork.fetch(img), img))
            except Exception:
                continue
    except Exception:
        images = []
    if len(images) < 2:
        grad = f"gradients=s=1920x1080:c0=0x0f172a:c1=0x2e1065:c2=0x0b3b4a:nb_colors=3:speed=0.004:duration={total + 1:.2f}:rate=24"
        _run([FFMPEG_BIN, "-y", "-v", "error", "-f", "lavfi", "-i", grad, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", out.name], work)
        return out, [], None
    fade = 1.0
    parts = []
    for i, dur in enumerate(shots):
        path, _ = images[i % len(images)]
        length = dur + (fade if i < len(shots) - 1 else 0)
        frames = int(length * 24) + 1
        z, x, y = MOVES[i % len(MOVES)]
        p = f"(on/{frames})"
        vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,"
              f"zoompan=z='{z.replace('p', p)}':x='{x.replace('p', p)}':y='{y.replace('p', p)}':d={frames}:s=1920x1080:fps=24,"
              f"eq=brightness=-0.10:saturation=0.9,format=yuv420p")
        part = work / f"shot{i}.mp4"
        _run([FFMPEG_BIN, "-y", "-v", "error", "-loop", "1", "-framerate", "24", "-i", str(path), "-vf", vf, "-t", f"{length:.2f}",
              "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", part.name], work)
        parts.append(part)
    # dissolve each shot into the next
    inputs, chain, offset, last = [], [], 0.0, "0:v"
    for i, part in enumerate(parts):
        inputs += ["-i", part.name]
    for i in range(1, len(parts)):
        offset += shots[i - 1]
        tag = f"v{i}"
        chain.append(f"[{last}][{i}:v]xfade=transition=fade:duration={fade}:offset={offset - 0.0:.3f}[{tag}]")
        last = tag
    if chain:
        _run([FFMPEG_BIN, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chain), "-map", f"[{last}]", "-c:v", "libx264",
              "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", out.name], work)
    else:
        shutil.copy(parts[0], out)
    return out, [artwork.credit(img) for _, img in images], images[0][0]


# ------------------------------------------------------------------ the Sanskrit opening, the music, the light
BED_UNDER = 16.0          # the music sits this many LU under the voice, and ducks a few dB more while someone speaks
FIRE_RE = re.compile(r"\b(?:fires?|flames?|agni|blaze|embers?|altars?|burn\w*|hearth|kindled?)\b|अग्नि|ज्वाला|लपट|यज्ञ|हवन|वेदी", re.I)
DAWN_RE = re.compile(r"\b(?:dawns?|ushas|sunrise|sunlight|surya|savitar|daybreak)\b|उषा|सूर्य|सविता|भोर|प्रभात|सवेर", re.I)


# hymns famous for one verse open with that verse (the Gayatri, the Mahamrityunjaya mantra)
FEATURED_VERSE = {"3.62": 10, "7.59": 12}


def _mantra_card(e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ref = _hymn_names(e)[0]
    if not re.fullmatch(r"\d+\.\d+", ref or ""):
        return None
    try:
        book, hymn = map(int, ref.split("."))
        return sanskrit.mantra_card(book, hymn, FEATURED_VERSE.get(ref, 1))
    except Exception:
        traceback.print_exc()
        return None


def _opening(sk: Optional[Dict[str, Any]], cast: Tuple[str, Optional[str]], work: Path) -> Optional[Dict[str, Any]]:
    """The hymn's first mantra recited in Sanskrit by the elder voice, to open the video (the card, its wav and its span)."""
    if not sk:
        return None
    reciter = yt_voice.RECITER                       # Sanskrit in a Hindi tone, in every video
    got = yt_voice.recite_sanskrit(sk["lines"], reciter, storyteller.CACHE / f"sanskrit_rv_{sk['ref'].replace('.', '_')}_{reciter}.mp3", work)
    if not got:
        if yt_voice.NATURAL_ONLY:
            raise RuntimeError("no voice could read the Sanskrit opening yet (daily quota)")
        return None
    return dict(sk, wav=got[0], span=(1.6, 1.6 + got[1]))


def _place(audio: bytearray, wav: Path, at: float, rate: int, width: int) -> None:
    with wave.open(str(wav), "rb") as wv:
        frames = wv.readframes(wv.getnframes())
    pos = int(at * rate) * width
    audio[pos:pos + len(frames)] = frames[: max(0, len(audio) - pos)]


def _lufs(path: Path) -> Optional[float]:
    r = subprocess.run([FFMPEG_BIN, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(found[-1]) if found else None


def _render(work: Path, bg: Path, ass: str, series: str, total: float, video: Path) -> None:
    """The paintings with the words on screen; the voice with a quiet music bed under it (its level set from both loudnesses,
    ducked while someone speaks, faded in and out), then loudness-normalised for YouTube."""
    vf = f"[0:v]scale=1920:1080,vignette=PI/4.4,ass={ass}[v]"
    args = ["-i", bg.name, "-i", "narration.wav"]
    graph = vf + ";[1:a]loudnorm=I=-16:TP=-1.5:LRA=9[a]"
    try:
        loop = music.bed(series) if series != "lectures" else None      # a classroom has no music
        voice, bed = _lufs(work / "narration.wav"), _lufs(loop)
    except Exception:
        traceback.print_exc()
        loop, voice, bed = None, None, None
    if loop and voice is not None and bed is not None:
        fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        args += ["-stream_loop", "-1", "-i", str(loop)]
        graph = (vf + f";[1:a]{fmt},asplit=2[n][k];"
                 f"[2:a]{fmt},atrim=0:{total:.2f},asetpts=PTS-STARTPTS,volume={voice - BED_UNDER - bed:.1f}dB,"
                 f"afade=t=in:d=3,afade=t=out:st={max(0.0, total - 4):.2f}:d=4[b];"
                 "[b][k]sidechaincompress=threshold=0.05:ratio=3:attack=40:release=700[d];"
                 "[n][d]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    _run([FFMPEG_BIN, "-y", "-v", "error", *args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
          "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          "-t", f"{total:.2f}", "-movflags", "+faststart", str(video.resolve())], work)


def _draw(add, layer: int, a: float, b: float, polys: List[List[Tuple[int, int]]], tags: str) -> None:
    """Filled polygons given in screen coordinates, as one drawing placed by its top-left corner."""
    mx, my = min(x for p in polys for x, _ in p), min(y for p in polys for _, y in p)
    path = " ".join(f"m {p[0][0] - mx} {p[0][1] - my} l " + " ".join(f"{x - mx} {y - my}" for x, y in p[1:]) for p in polys)
    add(layer, a, b, "Shade", f"{{\\an7\\pos({mx},{my}){tags}\\p1}}{path}{{\\p0}}")


def _frame(add, a: float, b: float, cx: int, cy: int, w: int, h: int) -> None:
    """A thin gold double rule with small diamonds at the corners and the middles, drawn outwards from the centre."""
    x0, y0, x1, y1 = cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2
    rules = []
    for inset, t in ((0, 2), (12, 1)):
        xa, ya, xb, yb = x0 + inset, y0 + inset, x1 - inset, y1 - inset
        rules += [[(xa, ya), (xb, ya), (xb, ya + t), (xa, ya + t)], [(xa, yb - t), (xb, yb - t), (xb, yb), (xa, yb)],
                  [(xa, ya + t), (xa + t, ya + t), (xa + t, yb - t), (xa, yb - t)], [(xb - t, ya + t), (xb, ya + t), (xb, yb - t), (xb - t, yb - t)]]
    d = 9
    gems = [[(px, py - d), (px + d, py), (px, py + d), (px - d, py)] for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1), (cx, y0), (cx, y1))]
    reveal = f"\\clip({cx},0,{cx + 1},1080)\\t(0,1100,\\clip({x0 - 20},0,{x1 + 20},1080))"
    _draw(add, 1, a, b, rules, f"\\c&H70C8F0&\\1a&H50&{reveal}\\fad(250,600)")
    _draw(add, 1, a, b, gems, f"\\c&H70C8F0&\\1a&H30&{reveal}\\fad(250,600)")


def _embers(add, a: float, b: float, rng) -> None:
    """Sparks rising from below and the warm flicker of firelight along the bottom of the frame."""
    if b - a < 3:
        return
    for _ in range(int((b - a) * 2.0)):
        s = rng.uniform(a, b - 2.5)
        life = rng.uniform(2.5, 5.0)
        x, y = rng.randint(60, 1860), rng.randint(1010, 1090)
        dx, dy = rng.randint(-140, 140), -rng.randint(320, 700)
        r = rng.choice((2, 3, 3, 4, 5))
        ms = int(life * 1000)
        col = rng.choice(("&H2A7BFF&", "&H1A5CFF&", "&H48A8FF&"))
        add(0, s, s + life, "Shade", f"{{\\an7\\move({x},{y},{x + dx},{y + dy},0,{ms})\\c{col}\\1a&H20&\\blur{r * 0.6:.1f}"
                                     f"\\fad(250,{int(ms * 0.55)})\\t(0,{ms},\\fscx35\\fscy35)\\p1}}"
                                     f"m {-r} 0 b {-r} {-r} {r} {-r} {r} 0 b {r} {r} {-r} {r} {-r} 0{{\\p0}}")
    t = a
    while t < b - 0.5:
        seg = min(8.0, b - t)
        flick = "".join(f"\\t({k * 400},{k * 400 + 380},\\1a&H{rng.choice(('C8', 'D0', 'D8', 'E0'))}&)" for k in range(int(seg * 1000 / 400)))
        fade = f"\\fad({1200 if t == a else 0},{1200 if t + seg >= b - 0.5 else 0})"
        add(0, t, t + seg, "Shade", f"{{\\an7\\pos(0,700)\\c&H1A5AD8&\\1a&HD0&\\blur90{fade}{flick}\\p1}}m 0 0 l 1920 0 1920 420 0 420{{\\p0}}")
        t += seg


def _rays(add, a: float, b: float, rng) -> None:
    """Long, soft shafts of morning light from the upper left, turning very slowly, with a glow where they begin."""
    if b - a < 3:
        return
    ms = int((b - a) * 1000)
    add(0, a, b, "Shade", "{\\an7\\pos(-260,-260)\\c&H9FE8FF&\\1a&HC0&\\blur120\\fad(2500,2500)\\p1}m 0 0 l 620 0 620 620 0 620{\\p0}")
    for k in range(6):
        ang = 18 + k * 8 + rng.uniform(-2.5, 2.5)
        wid = rng.randint(50, 130)
        drift = rng.choice((-3, 3))
        alpha = rng.choice(("D4", "DA", "E0"))
        add(0, a, b, "Shade", f"{{\\an7\\pos(-120,-120)\\org(-120,-120)\\frz{-ang:.1f}\\t(0,{ms},\\frz{-ang - drift:.1f})\\c&HA8E6FF&"
                              f"\\1a&H{alpha}&\\blur35\\fad(2500,2500)\\p1}}m 0 0 l 2800 0 2800 {wid} 0 {wid // 5}{{\\p0}}")


def _chapter_fx(add, a: float, b: float, text: str, rng, fire_min: int = 2) -> None:
    """Light that belongs to what is being told: dawn rays while the dawn or the sun is spoken of, embers for fire."""
    fire, dawn = len(FIRE_RE.findall(text or "")), len(DAWN_RE.findall(text or ""))
    if dawn and dawn >= fire:
        _rays(add, a, b, rng)
    elif fire >= fire_min:
        _embers(add, a, b, rng)


def _mantra_ass(add, scrim, op: Dict[str, Any], lang: str) -> None:
    """The first mantra in Devanagari, word by word as it is recited, in a gold frame, with its seer, deity and metre (and,
    for English viewers, a transliteration)."""
    a, b = op["span"]
    c0, c1 = max(0.1, a - 1.0), b + 1.3
    english = lang == "English"
    lines = op["lines"]
    scrim(c0, c1, 140, 230, 1640, 620, "40")
    _frame(add, c0, c1, 960, 540, 1600, 600)
    ref = op["ref"]
    kick = f"RIGVEDA {ref}  ·  THE ORIGINAL SANSKRIT" if english else f"ऋग्वेद {ref.translate(sanskrit.DIGITS)}  ·  मूल संस्कृत"
    add(2, c0, c1, "Kicker", f"{{\\pos(960,320)\\fad(600,500)}}{_esc(kick)}")
    words = [ln.split() for ln in lines]
    n = sum(len(x) for x in words) or 1
    ys = {1: [470], 2: [430, 530], 3: [390, 470, 550], 4: [380, 455, 530, 605]}[len(lines)]
    fs = max(40, min(62, int(2880 / max(len(x) for x in lines))))
    lead, step = (a - c0) * 1000, (b - a) * 1000 / n
    k = 0
    for ln, ws, y in zip(lines, words, ys):
        def body(tag: str, shown: str) -> str:
            return " ".join(f"{{{tag}&HFF&\\t({int(lead + (k + j) * step)},{int(lead + (k + j) * step + 450)},{tag}&H{shown}&)}}{_esc(wd)}"
                            for j, wd in enumerate(ws))
        # a soft gold glow behind the letters (the outline only, blurred), then the letters themselves
        add(2, c0, c1, "Mantra", f"{{\\pos(960,{y})\\q2\\fs{fs}\\fad(0,500)\\1a&HFF&\\bord7\\blur9\\3c&H2A78D0&}}{body(chr(92) + '3a', '60')}")
        add(3, c0, c1, "Mantra", f"{{\\pos(960,{y})\\q2\\fs{fs}\\fad(0,500)}}{body(chr(92) + 'alpha', '00')}")
        if english and len(lines) <= 3:
            t = int(lead + k * step)
            add(3, c0, c1, "Iast", f"{{\\pos(960,{ys[-1] + 85 + 42 * ys.index(y)})\\q2\\alpha&HFF&\\t({t},{t + 700},\\alpha&H00&)\\fad(0,500)}}"
                                   f"{_esc(sanskrit.iast(ln))}")
        k += len(ws)
    meta = []
    for label_en, label_hi, key in (("Seer", "ऋषि", "rishi"), ("Deity", "देवता", "devata"), ("Metre", "छन्द", "chhanda")):
        if op.get(key):
            meta.append(f"{label_en}: {sanskrit.iast(op[key]).title()}" if english else f"{label_hi}: {op[key]}")
    font = "" if english else "\\fnNirmala UI\\fsp0"          # letter spacing breaks Devanagari
    add(2, c0 + 0.8, c1, "Meta", f"{{\\pos(960,790)\\q2{font}\\fad(700,500)}}{_esc('   ·   '.join(meta))}")


# ------------------------------------------------------------------ words on screen
def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _esc(t: str) -> str:
    return (t or "").replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _norm(t: str) -> str:
    return re.sub(r"[^\w]+", " ", (t or "").lower()).strip()


def _doc_ass(doc: Dict[str, Any], times: List[Tuple[float, float]], total: float, speech_end: float, lang: str,
             kicker: str, source_line: str, opening: Optional[Dict[str, Any]] = None) -> str:
    """Title card after the cold open, a card at each chapter, quotations on screen while they are spoken, a source card."""
    serif = "Georgia" if lang == "English" else "Nirmala UI"
    sans = "Segoe UI" if lang == "English" else "Nirmala UI"
    sp = 4 if lang == "English" else 0                     # letter spacing breaks Devanagari
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Title,{serif},96,&H00F5F1EA,&H000000FF,&H64000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,5,200,200,0,1\n"
            f"Style: Kicker,{sans},30,&H0070C8F0,&H000000FF,&H64000000,&H00000000,1,0,0,0,100,100,{sp},0,1,2,0,5,200,200,0,1\n"
            f"Style: ChapNum,{sans},30,&H0070C8F0,&H000000FF,&H64000000,&H00000000,1,0,0,0,100,100,{sp},0,1,2,0,1,150,150,0,1\n"
            f"Style: Chap,{serif},68,&H00F5F1EA,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,1,150,300,0,1\n"
            f"Style: Quote,{serif},64,&H00F5F1EA,&H000000FF,&H64000000,&H00000000,0,{1 if lang == 'English' else 0},0,0,100,100,0,0,1,3,0,5,260,260,0,1\n"
            f"Style: Lower,{sans},30,&H00E6E6E6,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,1,150,150,0,1\n"
            "Style: Mantra,Nirmala UI,62,&H00F5F1EA,&H000000FF,&H00206AC8,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,120,120,0,1\n"
            "Style: Iast,Cambria,34,&H0090D8F0,&H000000FF,&H64000000,&H00000000,0,1,0,0,100,100,0,0,1,2,0,5,150,150,0,1\n"
            "Style: Meta,Segoe UI,28,&H00D8E0E6,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,1,0,1,2,0,5,150,150,0,1\n"
            "Style: Shade,Arial,20,&H00000000,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev: List[str] = []

    def add(layer: int, a: float, b: float, style: str, text: str) -> None:
        ev.append(f"Dialogue: {layer},{_ts(a)},{_ts(b)},{style},,0,0,0,,{text}")

    def by_word(text: str, first_ms: int, step_ms: int) -> str:
        # words appear one after another, each fading in over a third of a second
        out = []
        for k, word in enumerate(_esc(text).split()):
            t = first_ms + k * step_ms
            out.append(f"{{\\alpha&HFF&\\t({t},{t + 320},\\alpha&H00&)}}{word}")
        return " ".join(out)

    # dust in the light: a few soft motes drifting slowly upwards over the paintings the whole time
    rng = __import__("random").Random(len(doc.get("title", "")) * 7919 + int(total))
    for _ in range(int(total / 2.5)):
        a = rng.uniform(0, max(1.0, total - 6))
        life = rng.uniform(7, 13)
        x, y = rng.randint(80, 1840), rng.randint(300, 1080)
        dx, dy = rng.randint(-60, 60), -rng.randint(90, 240)
        r = rng.choice((2, 2, 3, 3, 4, 5))
        ms = int(life * 1000)
        add(0, a, a + life, "Shade", f"{{\\an7\\move({x},{y},{x + dx},{y + dy},0,{ms})\\c&HC8E6FF&\\1a&HA0&\\blur{1 + r / 2:.1f}"
                                     f"\\fad(1800,2200)\\p1}}m {-r} 0 b {-r} {-r} {r} {-r} {r} 0 b {r} {r} {-r} {r} {-r} 0{{\\p0}}")

    def scrim(a: float, b: float, x: int, y: int, w: int, h: int, alpha: str = "70") -> None:
        # a soft dark cloud behind words, so they read on any painting without a visible box
        add(0, a, b, "Shade", f"{{\\an7\\pos({x},{y})\\blur40\\1a&H{alpha}&\\fad(500,500)\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")

    if opening:
        _mantra_ass(add, scrim, opening, lang)
    for i, c in enumerate(doc["chapters"]):
        _chapter_fx(add, times[i][0] - (1.9 if i else 0.3), times[i][1] + 0.6, c.get("narration", ""), rng)

    # title card, in the pause after the cold open
    t0 = times[0][1] + 0.35
    t1 = t0 + 3.9
    scrim(t0, t1, 160, 330, 1600, 420, "60")
    _frame(add, t0, t1, 960, 540, 1640, 460)
    add(1, t0, t1, "Kicker", f"{{\\pos(960,430)\\fad(700,600)}}{_esc(kicker.upper() if lang == 'English' else kicker)}")
    add(1, t0, t1, "Title", f"{{\\pos(960,540)\\fad(300,600)\\fscx96\\fscy96\\t(0,3900,\\fscx100\\fscy100)}}{by_word(doc['title'], 150, 170)}")
    add(1, t0 + 0.5, t1, "Shade", "{\\an5\\pos(960,640)\\c&H70C8F0&\\clip(960,636,960,644)\\t(0,900,\\clip(760,636,1160,644))\\fad(0,600)\\p1}"
                                   "m 0 0 l 400 0 400 3 0 3{\\p0}")
    # where the words come from, as a lower third once the story starts
    add(1, t1 + 0.6, t1 + 6.5, "Lower", f"{{\\pos(150,1000)\\fad(500,500)}}{_esc(source_line)}")
    chap_word = "Chapter {n}" if lang == "English" else "अध्याय {n}"
    for i, c in enumerate(doc["chapters"]):
        a, b = times[i]
        if i:
            c0, c1 = (a - 1.9, a + 1.7) if i > 1 else (t1 + 0.2, t1 + 3.6)   # chapter 1's card waits for the title card
            scrim(c0, c1, 90, 760, 1150, 260)
            num = chap_word.format(n=i)
            add(1, c0, c1, "ChapNum", f"{{\\move(150,868,170,868)\\fad(450,500)}}{_esc(num.upper() if lang == 'English' else num)}")
            add(1, c0, c1, "Chap", f"{{\\move(150,960,170,960)\\fad(200,500)}}{by_word(c.get('title', ''), 250, 140)}")
        q = (c.get("quote") or "").strip().strip('"“”\'‘’')
        if len(q.split()) >= 4:                         # a word or two on its own reads oddly as a quotation card
            nar = _norm(c.get("narration", ""))
            key = _norm(q)[:40]
            pos = nar.find(key)
            if pos >= 0:
                t = a + (b - a) * pos / max(1, len(nar))
                show_from = max(t - 0.2, a + (1.8 if i else 0.3))
                dur = max(4.0, len(q) / 13)
                shown = f"“{q}”" if lang == "English" else f"“{q}”"
                scrim(show_from, show_from + dur, 200, 400, 1520, 280)
                step = int(min(380, dur * 550 / max(1, len(shown.split()))))    # about as fast as it is spoken
                add(2, show_from, show_from + dur, "Quote", f"{{\\pos(960,540)\\fad(250,600)\\fscx97\\fscy97\\t(0,{int(dur * 1000)},\\fscx100\\fscy100)}}"
                                                          f"{by_word(shown, 100, step)}")
    # the source, at the end
    scrim(speech_end, total, 160, 380, 1600, 320, "50")
    add(1, speech_end + 0.2, total, "Kicker", f"{{\\pos(960,470)\\fad(500,0)}}{'SOURCE' if lang == 'English' else 'स्रोत'}")
    add(1, speech_end + 0.2, total, "Quote", f"{{\\pos(960,560)\\fad(500,0)\\i0\\fs40}}{_esc(source_line)}")
    return head + "\n".join(ev) + "\n"


def _thumbnail(title: str, kicker: str, out: Path, work: Path, image: Optional[Path], lang: str) -> None:
    serif = "Georgia" if lang == "English" else "Nirmala UI"
    sp = 3 if lang == "English" else 0
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: K,Nirmala UI,32,&H0070C8F0,&H000000FF,&H50000000,&H00000000,1,0,0,0,100,100,{sp},0,1,3,0,1,70,500,250,1\n"
            f"Style: T,{serif},88,&H00F5F1EA,&H000000FF,&H40000000,&H00000000,1,0,0,0,100,100,0,0,1,5,0,1,70,420,70,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = [f"Dialogue: 0,0:00:00.00,0:00:01.00,K,,0,0,0,,{_esc(kicker.upper() if lang == 'English' else kicker)}",
          f"Dialogue: 0,0:00:00.00,0:00:01.00,T,,0,0,0,,{_esc(title)}"]
    (work / "thumb.ass").write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    src = ["-i", str(image)] if image else ["-f", "lavfi", "-i", "color=c=0x1b1330:s=1280x720:d=1"]
    vf = ("scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=brightness=-0.08:saturation=1.05,"
          "vignette=PI/3.2,ass=thumb.ass") if image else "ass=thumb.ass"
    subprocess.run([FFMPEG_BIN, "-y", "-v", "error", *src, "-vf", vf, "-frames:v", "1", "-q:v", "2", str(out.resolve())], cwd=str(work), capture_output=True)


def _mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _fallback_doc(sc: Dict[str, Any], e: Dict[str, Any]) -> Dict[str, Any]:
    """When no model can write the documentary script: the summary's own sections, with its thesis as the cold open."""
    chapters = [{"title": "", "narration": sc.get("thesis") or e["title"], "quote": ""}]
    chapters += [{"title": s.get("heading", ""), "narration": s.get("narration") or "", "quote": ""} for s in sc.get("sections") or []]
    return {"title": sc.get("title") or e["title"], "chapters": [c for c in chapters if c["narration"].strip()], "fallback": True}


# ------------------------------------------------------------------ a long video
def make_long(e: Dict[str, Any], series: str, code: str = "hi", progress=lambda p, m: None, voice: Optional[str] = None,
              subjects: Optional[List[str]] = None) -> Dict[str, Any]:
    lang, w = LANGS[code], WORDS[code]
    sc = script_for(e, lang)
    if not sc or not sc.get("sections"):
        raise RuntimeError(f"no {lang} summary to film")
    preset = voice if voice in yt_voice.PRESETS and yt_voice.PRESETS[voice]["language"] == lang else yt_voice.DEFAULT[code]
    accent = {"en-gb": "gb", "en-us": "us"}.get(preset, "")
    pid = f"{series}_{code}_{secrets.token_hex(4)}"
    out_dir, work = pack_dir(pid), TEMP_DIR / "youtube_work" / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        progress(5, "Writing the documentary script...")
        try:
            if lang == "Hindi" and script_for(e, "English"):
                # the Hindi script is translated from the checked English one (the free models write far better English;
                # a long script written straight in Hindi can fail outright, as it did for Chandrakanta)
                en_doc = storyteller.retell(e, script_for(e, "English"), "English", "gb")
                doc = storyteller.hindi_from_english(e, en_doc)
            else:
                doc = storyteller.retell(e, sc, lang, accent)
        except Exception:
            traceback.print_exc()
            # no summary-style fallback: it reads machine-made (and in Hindi it is not pure Hindi); better no video
            raise RuntimeError("no checked documentary script, so no video was made")
        chapters = doc["chapters"]
        sk = _mantra_card(e) if series == "vedas" else None
        cast = yt_voice.cast_for(series, code, preset, (sk or {}).get("rishi", ""))
        opening = _opening(sk, cast, work)
        # scene pictures (a plan in output/youtube/_scenes/plans/<entry id>.json, one list per chapter): fetched first, so a
        # used-up picture allowance stops the video before any voice is spent
        from backend.webapp import parallax, scenes as scn
        import json as _json
        plan_file = scn.SCENES / "plans" / f"{e['id']}.json"
        plan = _json.loads(plan_file.read_text(encoding="utf-8")) if plan_file.exists() else None
        if plan and len(plan) != len(chapters):
            plan = None
        pics = [[scn.picture(it.get("id") or f"{e['id']}-{i}-{k}", it["prompt"], it.get("seed", 7)) for k, it in enumerate(items)]
                for i, items in enumerate(plan)] if plan else []
        files, engine = yt_voice.voice_parts([c["narration"] for c in chapters], preset, work, progress=lambda m: progress(30, m),
                                             cast=cast)
        # the timeline: (the first mantra recited in Sanskrit,) a lead-in, the cold open, a pause for the title card, then each
        # chapter after its card
        durs = [yt_voice.to_wav(f, work / f"ch{i}.wav") for i, f in enumerate(files)]
        times, t = [], (opening["span"][1] + 1.8 if opening else 0.8)
        for i, d in enumerate(durs):
            if i == 1:
                t += 4.6
            elif i > 1:
                t += 2.3
            times.append((t, t + d))
            t += d
        speech_end = t + 1.2
        total = speech_end + 4.0
        with wave.open(str(work / "ch0.wav"), "rb") as w0:
            rate, width = w0.getframerate(), w0.getsampwidth()
        audio = bytearray(int(total * rate) * width)
        if opening:
            _place(audio, opening["wav"], opening["span"][0], rate, width)
        for i, (a, _) in enumerate(times):
            with wave.open(str(work / f"ch{i}.wav"), "rb") as wv:
                frames = wv.readframes(wv.getnframes())
            pos = int(a * rate) * width
            audio[pos:pos + len(frames)] = frames[: max(0, len(audio) - pos)]
        with wave.open(str(work / "narration.wav"), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(width)
            wv.setframerate(rate)
            wv.writeframes(bytes(audio))
        credit = (e.get("rights") or {}).get("attribution") or e["title"]
        series_name = SERIES[series] if code == "hi" else SERIES_EN[series]
        source_line = _source_line(e, credit)
        (work / "doc.ass").write_text(_doc_ass(doc, times, total, speech_end, lang, series_name, source_line, opening), encoding="utf-8")
        bounds = [0.0] + ([opening["span"][1] + 1.0] if opening else []) + [a - 1.9 for a, _ in times[1:]] + [total]
        english = script_for(e, "English") or {}
        if plan:
            progress(60, "Filming the scenes...")
            off = 1 if opening else 0                  # with a Sanskrit opening, the first span is the mantra card
            shots = []
            for i, items in enumerate(plan):
                s0, s1 = bounds[i + off], bounds[i + off + 1]
                for k, it in enumerate(items):
                    shots.append((pics[i][k], s0 + (s1 - s0) * k / len(items), s0 + (s1 - s0) * (k + 1) / len(items), it.get("move")))
            if off:
                shots[0] = (shots[0][0], 0.0, shots[0][2], shots[0][3])
            bg = parallax.render_sequence(shots, total, work / "bg.mp4")
            art_credits = []
            first_image = next((p for row in pics for p in row if p.suffix.lower() not in parallax.VIDEO_EXT), None)
        else:
            progress(60, "Filming the paintings...")
            bg, art_credits, first_image = _background(series, english.get("title") or "", bounds, work, artwork.subjects_for(sk) or subjects)
        video = out_dir / "video.mp4"
        progress(85, "Rendering...")
        _render(work, bg, "doc.ass", series, total, video)
        endcard.append_end_card(str(video), endcard.credit_for(e.get("rights"), e["title"]), doc["title"], lang, 4.0)
        _thumbnail(doc["title"], series_name, out_dir / "thumb.jpg", work, first_image, lang)
        sanskrit_lines = [" ".join(opening["lines"])] if opening else []
        (out_dir / "transcript.txt").write_text("\n\n".join(sanskrit_lines + [c["narration"].strip() for c in chapters]) + "\n",
                                                encoding="utf-8")
        probe = endcard._probe(str(video))
        marks = [f"0:00 {w['opening']}"] + [f"{_mmss(a - 1.9)} {c.get('title') or w['chapter'].format(n=i + 1)}"
                                            for i, (c, (a, _)) in enumerate(zip(chapters[1:], times[1:]))]
        hook = " ".join(re.split(r"(?<=[.!?।])\s+", chapters[0]["narration"].strip())[:2])
        lic = (e.get("rights") or {}).get("license") or ""
        desc = [hook, "", f"{w['chapters']}:"] + marks + ["", f"{w['original']}: {credit}", f"{w['rights']}: {lic}"]
        if series == "vedas":
            desc += [w["vedas_note"]]
        if art_credits:
            desc += ["", f"{w['paintings']}:"] + [f"• {c}" for c in art_credits]
        if opening:
            desc += ["", f"{'Sanskrit' if code == 'en' else 'मूल संस्कृत'}: {opening['source']} (sa.wikisource.org)"]
        scene_note = ({"en": "The scenes are AI pictures made for this video.", "hi": "दृश्य इस वीडियो के लिए बनाए गए AI चित्र हैं।"}[code]
                      if plan else None)
        desc += ["", f"{w['text']}: {e.get('page_url') or ''}", w["ai_note"]] + ([scene_note] if scene_note else []) + ["", w["hashtags"][series]]
        tags = [series_name] + w["tags"] + ([e.get("creator")] if e.get("creator") else []) + (w["vedas_tags"] if series == "vedas" else [])
        natural = not engine.startswith("edge")
        title = doc["title"]
        if series == "vedas" and sk:                  # the hymn's name and number, as people search for them
            _, name_hi, name_en = _hymn_names(e)
            tail = f" | {name_en}, Rigveda {sk['ref']}" if code == "en" else f" | {name_hi}, ऋग्वेद {sk['ref']}"
            title = title[: 100 - len(tail)].rstrip() + tail
        pack = {"id": pid, "series": series, "kind": "long", "entry_id": e["id"], "language": code, "title": title[:100],
                "description": "\n".join(desc), "tags": [t for t in tags if t][:15], "category_id": "27", "made_for_kids": False,
                "altered_or_synthetic": natural, "files": {"video": "video.mp4", "thumb": "thumb.jpg", "transcript": "transcript.txt"},
                "seconds": round(probe.get("duration") or 0, 1), "status": "draft", "created": time.time(),
                "voice": {"preset": preset, "engine": engine, "cast": list(cast), "music": True, "sanskrit_opening": bool(opening)},
                "checks": {"cleared_in": lib.territories(e), "no_film_clips": True, "voice": f"{preset} via {engine}", "chapters": len(marks),
                           "paintings": len(art_credits), "scenes": sum(len(x) for x in pics), "documentary_script": not doc.get("fallback"),
                           "stock_phrases_left": doc.get("banned_left", []), "quotes_from_source": doc.get("source_quoted", False),
                           "audio": bool(probe.get("audio")), "size_mb": round(video.stat().st_size / 1e6, 1)}}
        return _save(pack)
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _source_line(e: Dict[str, Any], credit: str) -> str:
    """A short credit for the screen, e.g. "Rigveda 10.90 · translated by Ralph T. H. Griffith"."""
    m = re.match(r"Rigveda, book (\d+), hymn (\d+), translated by ([^(]+)", credit)
    if m:
        return f"Rigveda {m[1]}.{m[2]} · translated by {m[3].strip()}"
    return re.sub(r"\s*\(.*?\)|, via Wikisource.*$|; public domain$", "", credit)[:110]


# ------------------------------------------------------------------ a hymn read in full ("Listen")
VERSE_RE = re.compile(r"^\s*(\d{1,3})\.?\s+(?=\S)", re.M)
HEADER_RE = re.compile(r"^\s*(?:THE HYMNS OF THE RIGVEDA\.?|BOOK THE [A-Z]+\.?|HYMN [IVXLC]+\.?)\s*$")
HYMN_NAMES_EN = {"1.1": "The Agni Sukta", "1.89": "The Hymn of Noble Thoughts", "5.85": "The Hymn to Varuna",
                 "10.10": "The Dialogue of Yama and Yami", "10.34": "The Gambler's Lament", "10.117": "The Hymn of Generosity",
                 "10.121": "The Hiranyagarbha Sukta", "10.90": "The Purusha Sukta", "1.164": "The Riddle Hymn",
                 "10.129": "The Hymn of Creation", "10.125": "The Hymn of Speech", "3.62": "The Hymn of the Gayatri Mantra",
                 "7.59": "The Hymn of the Mahamrityunjaya Mantra"}


def hymn_verses(e: Dict[str, Any]) -> List[str]:
    """The hymn in its public-domain translation, verse by verse. Griffith's pages number verses "1." or "2 ", and on some
    the first verse is left unnumbered under a header ("HYMN I." and the god's name), which is dropped."""
    lines, after_hymn = [], False
    for ln in storyteller.source_excerpt(e, 12000).splitlines():
        if HEADER_RE.match(ln):
            after_hymn = ln.strip().startswith("HYMN")
            continue
        if after_hymn and ln.strip():
            after_hymn = False
            if len(ln.split()) <= 4 and ln.strip().endswith("."):      # the god's name under "HYMN I."
                continue
        lines.append(ln)
    if any("[" in ln for ln in lines):
        return []       # editors' insertions in brackets: not Griffith's own text, so it is not read out as his
    parts = VERSE_RE.split("\n".join(lines).strip())
    verses = []
    first = re.sub(r"\s+", " ", parts[0]).strip()
    if first:
        verses.append(first)                                          # an unnumbered first verse
    for i in range(1, len(parts) - 1, 2):
        body = re.sub(r"\[[^\]]*\]", "", parts[i + 1])
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            verses.append(body)
    # a Wikisource note can follow the last verse ("See Wikisource:The Rig Veda/Book 10/Hymn 34 for notes."): not to be read
    verses = [re.sub(r"\s*[^.]*(?:for notes|Rig Veda/Book|Wikisource)[^.]*\.?\s*$", "", v).strip() for v in verses]
    return [v for v in verses if v][:60]


def _hymn_names(e: Dict[str, Any]) -> Tuple[str, str, str]:
    """(reference like "10.90", Hindi name like "पुरुष सूक्त", English name like "Purusha Sukta")."""
    credit = (e.get("rights") or {}).get("attribution") or ""
    m = re.match(r"Rigveda, book (\d+), hymn (\d+)", credit)
    ref = f"{m[1]}.{m[2]}" if m else ""
    name_hi = e["title"].split("—")[-1].strip() if "—" in e["title"] else e["title"]
    name_hi = re.sub(r"\s*\(.*?\)\s*", " ", name_hi).strip()
    slug = re.sub(r"^rigveda-\d+-\d+-", "", e["id"])
    words = [w for w in slug.split("-")[:-1] if w and not w.isdigit()]
    name_en = " ".join(w.capitalize() for w in words[:3]).replace("Sookta", "Sukta").replace("Sookt", "Sukta") or "the hymn"
    return ref, name_hi, HYMN_NAMES_EN.get(ref) or f"The {name_en}"


def _recital_ass(title: str, kicker: str, intro: Tuple[float, float], verses: List[str], times: List[Tuple[float, float]],
                 total: float, speech_end: float, lang: str, source_line: str, opening: Optional[Dict[str, Any]] = None) -> str:
    serif = "Georgia" if lang == "English" else "Nirmala UI"
    sans = "Segoe UI" if lang == "English" else "Nirmala UI"
    sp = 4 if lang == "English" else 0
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Title,{serif},92,&H00F5F1EA,&H000000FF,&H64000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,5,200,200,0,1\n"
            f"Style: Kicker,{sans},30,&H0070C8F0,&H000000FF,&H64000000,&H00000000,1,0,0,0,100,100,{sp},0,1,2,0,5,200,200,0,1\n"
            f"Style: Verse,{serif},60,&H00F5F1EA,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,5,250,250,0,1\n"
            f"Style: Small,{sans},30,&H00E6E6E6,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,200,200,0,1\n"
            "Style: Mantra,Nirmala UI,62,&H00F5F1EA,&H000000FF,&H00206AC8,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,120,120,0,1\n"
            "Style: Iast,Cambria,34,&H0090D8F0,&H000000FF,&H64000000,&H00000000,0,1,0,0,100,100,0,0,1,2,0,5,150,150,0,1\n"
            "Style: Meta,Segoe UI,28,&H00D8E0E6,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,1,0,1,2,0,5,150,150,0,1\n"
            "Style: Shade,Arial,20,&H00000000,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev: List[str] = []

    def add(layer: int, a: float, b: float, style: str, text: str) -> None:
        ev.append(f"Dialogue: {layer},{_ts(a)},{_ts(b)},{style},,0,0,0,,{text}")

    def scrim(a: float, b: float, x: int, y: int, w: int, h: int, alpha: str = "70") -> None:
        add(0, a, b, "Shade", f"{{\\an7\\pos({x},{y})\\blur40\\1a&H{alpha}&\\fad(500,500)\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")

    rng = __import__("random").Random(int(total * 100))
    if opening:
        _mantra_ass(add, scrim, opening, lang)
    a, b = intro
    scrim(a, b + 0.8, 160, 330, 1600, 420, "60")
    _frame(add, a, b + 0.8, 960, 540, 1640, 460)
    if times:
        _frame(add, times[0][0] - 0.3, times[-1][1] + 0.7, 960, 550, 1660, 580)
        _chapter_fx(add, times[0][0], times[-1][1], " ".join(verses), rng, fire_min=3)
    add(1, a, b + 0.8, "Kicker", f"{{\\pos(960,430)\\fad(600,500)}}{_esc(kicker.upper() if lang == 'English' else kicker)}")
    add(1, a, b + 0.8, "Title", f"{{\\pos(960,540)\\fad(600,500)}}{_esc(title)}")
    word = "VERSE {n}" if lang == "English" else "मंत्र {n}"
    for n, (v, (s, e)) in enumerate(zip(verses, times), 1):
        c0, c1 = s - 0.3, e + 0.7
        scrim(c0, c1, 170, 300, 1580, 500)
        add(1, c0, c1, "Kicker", f"{{\\pos(960,360)\\fad(300,400)}}{word.format(n=n)}")
        add(2, c0, c1, "Verse", f"{{\\pos(960,590)\\fad(450,450)}}{_esc(v)}")
    scrim(speech_end, total, 160, 380, 1600, 320, "50")
    add(1, speech_end + 0.2, total, "Kicker", f"{{\\pos(960,470)\\fad(500,0)}}{'SOURCE' if lang == 'English' else 'स्रोत'}")
    add(1, speech_end + 0.2, total, "Small", f"{{\\pos(960,550)\\fad(500,0)}}{_esc(source_line)}")
    return head + "\n".join(ev) + "\n"


def make_recital(e: Dict[str, Any], code: str = "hi", voice: Optional[str] = None, progress=lambda p, m: None) -> Dict[str, Any]:
    """A Rigveda hymn read in full: Griffith's own words in English, a checked Hindi translation of them in Hindi."""
    lang, w = LANGS[code], WORDS[code]
    preset = voice if voice in yt_voice.PRESETS and yt_voice.PRESETS[voice]["language"] == lang else yt_voice.DEFAULT[code]
    verses_en = hymn_verses(e)
    if not 2 <= len(verses_en) <= 60:
        raise RuntimeError("no verse-by-verse text to read")
    verses_en = storyteller.proofread_verses_en(e, verses_en)          # scanning mistakes ("cats" for "eats") corrected
    verses = verses_en if code == "en" else storyteller.translate_verses(e, verses_en)["verses"]
    ref, name_hi, name_en = _hymn_names(e)
    book, hymn = (ref.split(".") + ["", ""])[:2]
    credit = (e.get("rights") or {}).get("attribution") or e["title"]
    if code == "en":
        title = f"{name_en}, read in full | Rigveda {ref}"
        card = name_en
        intro = f"{name_en}. Rigveda, book {book}, hymn {hymn}, in the translation by Ralph T. H. Griffith."
        outro = "Here the hymn ends."
    else:
        title = f"{name_hi}, पूरा सूक्त हिंदी में | ऋग्वेद {ref}"
        card = name_hi
        intro = f"{name_hi}। ऋग्वेद, मंडल {book}, सूक्त {hymn}, हिंदी अनुवाद में।"
        outro = "यहाँ सूक्त समाप्त होता है।"
    pid = f"audio_{code}_{secrets.token_hex(4)}"
    out_dir, work = pack_dir(pid), TEMP_DIR / "youtube_work" / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        texts = [intro] + verses + [outro]
        sk = _mantra_card(e)
        cast = yt_voice.cast_for("audio", code, preset, (sk or {}).get("rishi", ""))
        opening = _opening(sk, cast, work)
        files, engine = yt_voice.voice_parts(texts, preset, work, progress=lambda m: progress(30, m), cast=cast,
                                             whole=["Narrator"] + ["Poet"] * len(verses) + ["Narrator"])
        durs = [yt_voice.to_wav(f, work / f"v{i}.wav") for i, f in enumerate(files)]
        times, t = [], (opening["span"][1] + 1.8 if opening else 0.8)
        for i, d in enumerate(durs):
            if i == 1:
                t += 1.4
            elif i == len(durs) - 1:
                t += 1.6
            elif i > 1:
                t += 1.0
            times.append((t, t + d))
            t += d
        speech_end = t + 1.2
        total = speech_end + 4.0
        with wave.open(str(work / "v0.wav"), "rb") as w0:
            rate, width = w0.getframerate(), w0.getsampwidth()
        audio = bytearray(int(total * rate) * width)
        if opening:
            _place(audio, opening["wav"], opening["span"][0], rate, width)
        for i, (a, _) in enumerate(times):
            with wave.open(str(work / f"v{i}.wav"), "rb") as wv:
                frames = wv.readframes(wv.getnframes())
            pos = int(a * rate) * width
            audio[pos:pos + len(frames)] = frames[: max(0, len(audio) - pos)]
        with wave.open(str(work / "narration.wav"), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(width)
            wv.setframerate(rate)
            wv.writeframes(bytes(audio))
        kicker = f"Rigveda {ref}" if code == "en" else f"ऋग्वेद {ref}"
        source_line = _source_line(e, credit)
        (work / "recital.ass").write_text(_recital_ass(card, kicker, times[0], verses, times[1:-1], total, speech_end, lang, source_line, opening),
                                          encoding="utf-8")
        bg, art_credits, first_image = _background("vedas", name_en, [0.0, total], work, artwork.subjects_for(sk))
        video = out_dir / "video.mp4"
        _render(work, bg, "recital.ass", "audio", total, video)
        endcard.append_end_card(str(video), endcard.credit_for(e.get("rights"), e["title"]), card, lang, 4.0)
        _thumbnail(card, SERIES_EN["audio"] if code == "en" else SERIES["audio"], out_dir / "thumb.jpg", work, first_image, lang)
        sanskrit_lines = [" ".join(opening["lines"])] if opening else []
        (out_dir / "transcript.txt").write_text("\n\n".join(sanskrit_lines + [intro] + verses + [outro]) + "\n", encoding="utf-8")
        probe = endcard._probe(str(video))
        if code == "en":
            desc = [f"The whole of Rigveda {ref} ({name_en}), read aloud in Ralph T. H. Griffith's public-domain English translation "
                    "(The Hymns of the Rigveda, 1889-1892). Griffith's wording is kept exactly as he wrote it."]
        else:
            desc = [f"ऋग्वेद {ref} ({name_hi}) का पूरा सूक्त, राल्फ टी. एच. ग्रिफ़िथ के सार्वजनिक (public-domain) अंग्रेज़ी अनुवाद (1889-1892) "
                    "से हिंदी में अनूदित करके पढ़ा गया है। यह अनुवाद का अनुवाद है; परंपरागत और विद्वानों के अर्थ कई जगह अलग होते हैं।"]
        desc += ["", f"{w['original']}: {credit}", f"{w['text']}: {e.get('page_url') or ''}"]
        if art_credits:
            desc += ["", f"{w['paintings']}:"] + [f"• {c}" for c in art_credits]
        if opening:
            desc += ["", f"{'Sanskrit' if code == 'en' else 'मूल संस्कृत'}: {opening['source']} (sa.wikisource.org)"]
        desc += ["", w["ai_note"], "", w["hashtags"]["audio"]]
        tags = ([SERIES_EN["audio"], "Rigveda", "Vedas", "Hindu hymns", "Griffith", name_en] if code == "en"
                else [SERIES["audio"], "ऋग्वेद", "वेद", "सूक्त", name_hi, "Rigveda in Hindi"])
        pack = {"id": pid, "series": "audio", "kind": "long", "entry_id": e["id"], "language": code, "title": title[:100],
                "description": "\n".join(desc), "tags": tags[:15], "category_id": "27", "made_for_kids": False,
                "altered_or_synthetic": not engine.startswith("edge"),
                "files": {"video": "video.mp4", "thumb": "thumb.jpg", "transcript": "transcript.txt"},
                "seconds": round(probe.get("duration") or 0, 1), "status": "draft", "created": time.time(),
                "voice": {"preset": preset, "engine": engine, "cast": list(cast), "music": True, "sanskrit_opening": bool(opening)},
                "checks": {"cleared_in": lib.territories(e), "no_film_clips": True, "voice": f"{preset} via {engine}", "verses": len(verses),
                           "paintings": len(art_credits), "text": "Griffith's own words" if code == "en" else "Hindi translation of Griffith",
                           "audio": bool(probe.get("audio")), "size_mb": round(video.stat().st_size / 1e6, 1)}}
        return _save(pack)
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ a maths lesson Short
def make_maths(trick: str, seed: int, code: str = "hi", voice: Optional[str] = None) -> Dict[str, Any]:
    lang = LANGS[code]
    preset = voice if voice in yt_voice.PRESETS and yt_voice.PRESETS[voice]["language"] == lang else yt_voice.DEFAULT[code]
    ls = lessons.lesson(trick, seed)
    pid = f"maths_{code}_{secrets.token_hex(4)}"
    out_dir, work = pack_dir(pid), TEMP_DIR / "youtube_work" / pid
    try:
        files = lessons.render_lesson(ls, out_dir, work, lang, preset)
        meta = lessons.metadata(ls, lang, files["examples"])
        probe = endcard._probe(str(out_dir / files["video"]))
        natural = not files["engine"].startswith("edge")
        pack = dict(meta, id=pid, series="maths", kind="short", lesson_key=f"{trick}:{seed}:{code}", language=code,
                    files={"video": files["video"], "thumb": files["thumb"]}, seconds=round(probe.get("duration") or 0, 1),
                    status="draft", created=time.time(), altered_or_synthetic=natural, voice={"preset": preset, "engine": files["engine"]},
                    checks={"cleared_in": ["ALL"], "no_film_clips": True, "examples_checked_by_code": True, "examples": files["examples"],
                            "practice": files["practice"], "voice": f"{preset} via {files['engine']}", "audio": bool(probe.get("audio")),
                            "short_limit_ok": (probe.get("duration") or 999) <= 180, "size_mb": round((out_dir / files["video"]).stat().st_size / 1e6, 1)})
        return _save(pack)
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _next_lessons(n: int, codes: List[str]) -> List[tuple]:
    """Lessons not yet made in any of the requested languages; the same lesson is made in each of them."""
    used = {p.get("lesson_key") for p in queue() if p.get("status") != "rejected"}
    out, seed = [], 1
    while len(out) < n and seed < 500:
        for t in lessons.TRICKS:
            if len(out) < n and all(f"{t}:{seed}:{c}" not in used for c in codes):
                out.append((t, seed))
        seed += 1
    return out


# ------------------------------------------------------------------ batches
def start_batch(series: str, count: int, language: str = "both", voices: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if series not in SERIES:
        raise ValueError("Unknown series.")
    if language not in ("hi", "en", "both"):
        raise ValueError("Choose Hindi, English or both.")
    with _lock:
        if STATE["running"]:
            return dict(STATE)
        STATE.update(running=True, series=series, language=language, done=0, wanted=count * (2 if language == "both" else 1),
                     message="Starting...", errors=[], asked=[])
    threading.Thread(target=_run_batch, args=(series, count, language, voices or {}), daemon=True, name="youtube-batch").start()
    return dict(STATE)


def _run_batch(series: str, count: int, language: str, voices: Dict[str, str]) -> None:
    codes = ["hi", "en"] if language == "both" else [language]
    try:
        if series == "maths":
            for trick, seed in _next_lessons(count, codes):
                for code in codes:
                    STATE["message"] = f"{LANGS[code]} lesson {STATE['done'] + 1} of {STATE['wanted']}: {lessons.TRICKS[trick]['title']['English']}"
                    try:
                        make_maths(trick, seed, code, voices.get(code))
                        STATE["done"] += 1
                    except Exception as e:
                        traceback.print_exc()
                        STATE["errors"].append(f"{trick} ({code}): {str(e)[:160]}")
            return
        for code in codes:
            todo = candidates(series, count, code)
            if code == "en" and len(todo) < count:      # translate finished Hindi summaries first (a few minutes each)
                from backend.webapp import translate
                for e in untranslated(series, count - len(todo)):
                    STATE["message"] = f"Translating into English: {e['title'][:50]}"
                    try:
                        translate.translate_title(e, "English")
                    except Exception as ex_:
                        STATE["errors"].append(f"translation of {e['title'][:40]}: {str(ex_)[:120]}")
                todo = candidates(series, count, code)
            if len(todo) < count:                        # ask the builder to make the missing summaries first
                from backend.webapp import builder
                for e in waiting(series, count - len(todo)):
                    try:
                        builder.request(e["id"])
                        STATE["asked"].append(e["title"][:60])
                    except KeyError:
                        pass
            for k, e in enumerate(todo, 1):
                label = f"{LANGS[code]} video {k} of {len(todo)}: {e['title'][:50]}"
                STATE["message"] = label
                try:
                    if series == "audio":
                        make_recital(e, code, voices.get(code))
                    else:
                        make_long(e, series, code, lambda p, m: STATE.update(message=f"{label} ({m})"), voices.get(code))
                    STATE["done"] += 1
                except Exception as ex_:
                    traceback.print_exc()
                    STATE["errors"].append(f"{e['title'][:50]} ({code}): {str(ex_)[:160]}")
    finally:
        STATE["running"] = False
        made = f"Done: {STATE['done']} made" + (f", {len(STATE['errors'])} failed" if STATE["errors"] else "") + "."
        if STATE["asked"]:
            made += f" The builder was asked to make {len(STATE['asked'])} more summaries first; try again later for those."
        STATE["message"] = made


# stories read aloud (reading.py) share the queue and the pack page
SERIES.setdefault("stories", "कहानी सुनिए")
SERIES_EN.setdefault("stories", "Stories, Read Aloud")
