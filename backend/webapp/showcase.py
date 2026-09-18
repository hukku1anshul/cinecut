"""
Showcase videos for the CineCut YouTube channel: finished MP4s made from titles already in the library, to show people
what the app's shortened versions look like (advertising, not monetised). Nothing is written again: each video is
rendered from the saved recipe, voiced with the natural Gemini voices the viewer approved, and credited.

    film         the recipe's clips of the public-domain film, the narrator over each clip (the film's sound drops to 22%
                 while the narrator speaks, as in the app's player); a clip that ends before its line holds its last frame
    story        the own-words summary as narrated slides (headings and key points, no camera movement)
    lecture      the same, read by the teachers' voices
    audio story  the summary read aloud over one still card that names the part being read

Every video starts with a title card and ends with the source credit and the "made with AI" card. Each gets a
description.txt with the credit and licence for the YouTube description. Output: output/showcase/<type>/<nn>-<slug>/.
"""
import json
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.config import FFMPEG_BIN, FFPROBE_BIN, OUTPUT_DIR
from backend.video_engine import endcard
from backend.video_engine import explainer as ex
from backend.webapp import library as lib
from backend.webapp import yt_voice

OUT = OUTPUT_DIR / "showcase"
SR = ex.SAMPLE_RATE
PRESET = {"Hindi": "hi", "English": "en-gb"}
CODE = {"Hindi": "hi", "English": "en"}
W, H, FPS = 1920, 1080, 24
FONT = "Nirmala UI"
DUCK = 0.22                                   # the film's own sound while the narrator speaks (as in the app's player)
TYPE_NAME = {"film": {"Hindi": "फ़िल्म, छोटे रूप में", "English": "A FILM, SHORTENED"},
             "story": {"Hindi": "कहानी, छोटे रूप में", "English": "A STORY, SHORTENED"},
             "lecture": {"Hindi": "लेक्चर, छोटे रूप में", "English": "A LECTURE, SHORTENED"},
             "audio": {"Hindi": "सुनिए: कहानी, छोटे रूप में", "English": "LISTEN: A STORY, SHORTENED"}}
PROMO = {"Hindi": "यह छोटा रूप CineCut ने बनाया है", "English": "Shortened with CineCut"}


def _run(cmd: List[str], cwd: Optional[Path] = None) -> None:
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:300])


def _dur(path: Path) -> float:
    r = subprocess.run([FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _slug(t: str) -> str:
    return re.sub(r"[^\wऀ-ॿ]+", "-", t.lower()).strip("-")[:48] or "title"     # keeps Hindi vowel signs


def _esc(t: str) -> str:
    return (t or "").replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _ts(t: float) -> str:
    return ex._ts(t)


def _ass_head(styles: str) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


CARD_STYLES = (f"Style: Kicker,{FONT},34,{ex.C_ACCENT},{ex.C_ACCENT},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1\n"
               f"Style: Title,{FONT},92,{ex.C_TEXT},{ex.C_TEXT},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1\n"
               f"Style: Heading,{FONT},62,{ex.C_TEXT},{ex.C_TEXT},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1\n"
               f"Style: Small,{FONT},32,{ex.C_MUTED},{ex.C_MUTED},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1\n")


def _card(out: Path, seconds: float, lines: List[Tuple[str, str, int]], work: Path) -> None:
    """A still title card: (style, text, y) lines on the explainer's dark ground, with silent audio."""
    ev = "".join(f"Dialogue: 0,{_ts(0)},{_ts(seconds)},{st},,0,0,0,,{{\\pos(140,{y})\\fad(400,300)}}{_esc(tx)}\n" for st, tx, y in lines)
    (work / f"{out.stem}.ass").write_text(_ass_head(CARD_STYLES) + ev, encoding="utf-8")
    _run([FFMPEG_BIN, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={ex.BG}:s={W}x{H}:r={FPS}:d={seconds:.2f}",
          "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-vf", f"ass={out.stem}.ass", "-t", f"{seconds:.2f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
          "-ar", "48000", "-ac", "2", out.name], cwd=work)


def _voice(texts: List[str], language: str, series: str, style, work: Path, log: Callable[[str], None]) -> List[Path]:
    """One WAV per text, in the natural voices only (the batch waits for Gemini's quota rather than use another voice)."""
    code, preset = CODE[language], PRESET[language]
    cast = yt_voice.cast_for(series, code, preset)
    files, engine = yt_voice.voice_parts(texts, preset, work / "voice", teacher=style, cast=cast, progress=lambda m: log(f"   voice: {m}"))
    log(f"   voiced {len(files)} part(s) with {engine} ({cast[0]})")
    wavs = []
    for i, f in enumerate(files):
        w = work / f"v{i:02d}.wav"
        _run([FFMPEG_BIN, "-y", "-v", "error", "-i", str(f), "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(w)])
        wavs.append(w)
    return wavs


def _join(wavs: List[Path], out: Path, gap: float = 0.8, tail: float = 1.0) -> List[Tuple[float, float]]:
    frames, times = bytearray(), []
    for w in wavs:
        with wave.open(str(w), "rb") as r:
            data = r.readframes(r.getnframes())
        t0 = len(frames) / 2 / SR
        frames += data
        times.append((t0, len(frames) / 2 / SR))
        frames += b"\x00\x00" * int(SR * gap)
    frames += b"\x00\x00" * int(SR * tail)
    with wave.open(str(out), "wb") as r:
        r.setnchannels(1)
        r.setsampwidth(2)
        r.setframerate(SR)
        r.writeframes(bytes(frames))
    return times


def _script(e: Dict[str, Any], language: str) -> Dict[str, Any]:
    return (e.get("scripts") or {}).get(language) or e["script"]


def _blocks(e: Dict[str, Any], language: str) -> List[str]:
    """The narration as the app plays it: the opening, then one block per section (from the saved pieces)."""
    pieces = (e.get("pieces") or {}).get(language) or []
    n = len(_script(e, language)["sections"])
    return [" ".join(p["text"] for p in pieces if p["section"] == i).strip() for i in range(-1, n)]


def _concat(parts: List[Path], out: Path, work: Path) -> None:
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    _run([FFMPEG_BIN, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
          "-movflags", "+faststart", str(out)])


def _credit(e: Dict[str, Any]) -> str:
    return endcard.credit_for(e.get("rights"), e.get("source_title") or e["title"])


def _name(e: Dict[str, Any], kind: str, language: str) -> str:
    """Lectures are named by their summary (YouTube lecture titles read like "UPPCS  2021 Geography Class Episode 01/...");
    films, stories and books keep the work's own name."""
    if kind == "lecture":
        return _script(e, language).get("title") or lib.display_title(e["title"])
    return lib.display_title(e["title"])


def _title_card(e: Dict[str, Any], kind: str, language: str, work: Path) -> Path:
    card = work / "title.mp4"
    year = f" ({e['year']})" if e.get("year") and kind == "film" else ""
    under = (e.get("source_title") or e["title"]) + (f" · {e['creator']}" if e.get("creator") else "") if kind == "lecture" else (e.get("creator") or "")
    _card(card, 4.5, [("Kicker", TYPE_NAME[kind][language], 330), ("Title", _name(e, kind, language) + year, 390),
                      ("Small", under[:110], 600), ("Small", PROMO[language], 900)], work)
    return card


# ------------------------------------------------------------------ the four kinds
def make_slides(e: Dict[str, Any], kind: str, language: str, work: Path, out: Path, log) -> None:
    """Story or lecture: the saved summary as narrated slides."""
    sc = _script(e, language)
    blocks = _blocks(e, language)
    wavs = _voice(blocks, language, "lectures" if kind == "lecture" else "classics", True if kind == "lecture" else "story", work, log)
    narration = work / "narration.wav"
    times = _join(wavs, narration)
    secs = sc["sections"]
    recap = secs[-1].get("heading") == ex.RECAP_HEADING.get(language)
    outline = {"title": _name(e, kind, language), "sections": secs[:-1] if recap else secs,
               "takeaways": (secs[-1].get("key_points") if recap else None) or sc.get("takeaways") or []}
    total = times[-1][1] + 1.2
    source = f"{e.get('source_title') or e['title']} · {e.get('creator') or ''} · {(e.get('rights') or {}).get('license') or ''}"
    ass = ex.slides_ass(outline, times, total, source, language, (total, total))
    body = work / "body.mp4"
    ex.render_video(work, narration, ass, total, body)
    _concat([_title_card(e, kind, language, work), _stereo(body, work)], out, work)


def make_audio(e: Dict[str, Any], language: str, work: Path, out: Path, log) -> None:
    """Audio story: the summary read aloud over one still card; the card names the part being read."""
    sc = _script(e, language)
    blocks = _blocks(e, language)
    wavs = _voice(blocks, language, "classics", "story", work, log)
    narration = work / "narration.wav"
    times = _join(wavs, narration)
    total = times[-1][1] + 1.2
    heads = [""] + [s.get("heading") or "" for s in sc["sections"]]
    ev = [f"Dialogue: 0,{_ts(0)},{_ts(total)},Kicker,,0,0,0,,{{\\pos(140,300)}}{_esc(TYPE_NAME['audio'][language])}",
          f"Dialogue: 0,{_ts(0)},{_ts(total)},Title,,0,0,0,,{{\\pos(140,360)}}{_esc(lib.display_title(e['title']))}",
          f"Dialogue: 0,{_ts(0)},{_ts(total)},Small,,0,0,0,,{{\\pos(140,520)}}{_esc(e.get('creator') or '')}",
          f"Dialogue: 0,{_ts(0)},{_ts(total)},Small,,0,0,0,,{{\\pos(140,960)}}{_esc(PROMO[language])}"]
    for i, (s, _) in enumerate(times):
        nxt = times[i + 1][0] if i + 1 < len(times) else total
        head = heads[i] if i < len(heads) else ""
        if head:
            ev.append(f"Dialogue: 0,{_ts(s)},{_ts(nxt)},Heading,,0,0,0,,{{\\pos(140,680)\\fad(400,300)}}{_esc(head)}")
    body = work / "body.mp4"
    ex.render_video(work, narration, _ass_head(CARD_STYLES) + "\n".join(ev) + "\n", total, body)
    _concat([_stereo(body, work)], out, work)


def _stereo(src: Path, work: Path) -> Path:
    """The slide renderer writes mono 24 kHz sound; the joined video uses 48 kHz stereo throughout."""
    dst = work / f"{src.stem}_st.mp4"
    _run([FFMPEG_BIN, "-y", "-v", "error", "-i", str(src), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(dst)])
    return dst


def make_film(e: Dict[str, Any], language: str, work: Path, out: Path, log) -> None:
    """Film: each kept clip of the public-domain film, with its narration line spoken over the start of the clip."""
    url = e["source"]["url"]
    lines = {n["clip"]: n["text"] for n in (e.get("narration") or {}).get(language) or [] if n.get("text")}
    order = sorted(lines)
    wavs = dict(zip(order, _voice([lines[i] for i in order], language, "classics", "story", work, log)))
    parts = [_title_card(e, "film", language, work)]
    fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FPS}"
    for i, c in enumerate(e["clips"]):
        raw = work / f"clip{i:02d}.mp4"
        length = round(c["end"] - c["start"], 2)
        log(f"   clip {i + 1}/{len(e['clips'])}: {c['start']:.0f}-{c['end']:.0f} s")
        _run([FFMPEG_BIN, "-y", "-v", "error", "-ss", f"{c['start']:.2f}", "-i", url, "-t", f"{length:.2f}", "-vf", fit,
              "-af", "aresample=48000", "-ac", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "160k", str(raw)])
        if not _has_audio(raw):                                   # a silent print: give it silence to mix with
            with_sound = work / f"clip{i:02d}_a.mp4"
            _run([FFMPEG_BIN, "-y", "-v", "error", "-i", str(raw), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
                  "-c:v", "copy", "-c:a", "aac", str(with_sound)])
            raw = with_sound
        if i not in wavs:
            parts.append(raw)
            continue
        speech = _dur(wavs[i]) + 0.4
        hold = max(0.0, speech + 0.6 - _dur(raw))                # the line is longer than the clip: hold the last frame
        mixed = work / f"clip{i:02d}_n.mp4"
        _run([FFMPEG_BIN, "-y", "-v", "error", "-i", str(raw), "-i", str(wavs[i]), "-filter_complex",
              f"[0:v]tpad=stop_mode=clone:stop_duration={hold:.2f}[v];"
              f"[0:a]apad=pad_dur={hold:.2f},volume='if(lt(t,{speech:.2f}),{DUCK},1)':eval=frame[bed];"
              f"[1:a]aresample=48000,pan=stereo|c0=c0|c1=c0,adelay=300|300,volume=1.6[vo];"
              f"[bed][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
              "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(mixed)])
        parts.append(mixed)
    _concat(parts, out, work)


def _has_audio(p: Path) -> bool:
    r = subprocess.run([FFPROBE_BIN, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return bool(r.stdout.strip())


MAKERS = {"film": make_film, "story": lambda e, l, w, o, log: make_slides(e, "story", l, w, o, log),
          "lecture": lambda e, l, w, o, log: make_slides(e, "lecture", l, w, o, log), "audio": make_audio}


def description(e: Dict[str, Any], kind: str, language: str, note: str = "") -> str:
    r = e.get("rights") or {}
    lines = [lib.display_title(e["title"]), "",
             {"film": "A shortened version of the film, with narration.", "story": "A short retelling of the story, in our own words.",
              "lecture": "A short summary of the lecture, in our own words.", "audio": "A short retelling to listen to, in our own words."}[kind],
             "Shortened with CineCut. The narration, voice and summary were made with AI.", "",
             f"Source: {r.get('attribution') or e.get('source_title') or e['title']}",
             f"Licence: {r.get('license') or 'public domain'}"]
    if note:
        lines += ["", note]
    lines += ["", "YouTube: mark this video as altered or synthetic content (AI voice)."]
    return "\n".join(lines) + "\n"


def make(e: Dict[str, Any], kind: str, language: str, n: int, note: str = "", log: Callable[[str], None] = print) -> Path:
    folder = OUT / kind / f"{n:02d}-{_slug(_name(e, kind, language))}"
    out = folder / "video.mp4"
    if out.exists():
        log(f"{kind} {n}: already made ({out})")
        return out
    work = folder / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log(f"{kind} {n}: {e['title'][:60]} ({language})")
    tmp = folder / "video.part.mp4"
    MAKERS[kind](e, language, work, tmp, log)
    endcard.append_end_card(str(tmp), _credit(e), lib.display_title(e["title"]), language)
    tmp.replace(out)
    (folder / "description.txt").write_text(description(e, kind, language, note), encoding="utf-8")
    (folder / "made.json").write_text(json.dumps({"id": e["id"], "kind": kind, "language": language, "seconds": round(_dur(out), 1),
                                                  "made_in_s": round(time.time() - t0)}, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    log(f"{kind} {n}: done, {_dur(out) / 60:.1f} min, in {time.time() - t0:.0f} s -> {out}")
    return out
