"""
Lectures in brief, at the chalkboard: a short own-words version of a Creative Commons lecture (CC BY only; never NPTEL,
NonCommercial or ShareAlike), taught by a teacher's voice while the points are written up in chalk as they are said: a
heading for each part, the points in a steady hand, the key point underlined in yellow and ticked. The board is filmed as
if by a camera on a tripod (a very slow drift) and wiped between parts. No music: a classroom has none.

A lecture is prepared as JSON in output/youtube/_lectures/<id>.json: titles, the spoken introduction, the parts (heading,
narration, board lines, each with the words it is written at), the closing exercise with its answers, and the credit line
the CC BY licence asks for (with the changes made).
"""
import json
import random
import re
import secrets
import shutil
import subprocess
import time
import traceback
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List

from backend.config import FFMPEG_BIN, OUTPUT_DIR, TEMP_DIR
from backend.video_engine import endcard
from backend.webapp import lessons as L, youtube as yt, yt_voice

LECTURES = OUTPUT_DIR / "youtube" / "_lectures"
W, H = 1920, 1080
HAND = {"English": "Segoe Print", "Hindi": "Nirmala UI"}        # Segoe Print draws H₂SO₄, sp³d², σ, π and → in one hand
X0 = 170
STYLES = "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, " \
         "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"


def _head(lang: str) -> str:
    hand, sp = HAND[lang], (2 if lang == "English" else 0)
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n" + STYLES +
            f"Style: Kicker,Nirmala UI,28,{L.YELLOW},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,{sp},0,1,0,0,7,0,0,0,1\n"
            f"Style: Head,{hand},72,{L.YELLOW},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Point,{hand},56,{L.CHALK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Title,{hand},{92 if lang == 'English' else 86},{L.CHALK},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Foot,Nirmala UI,24,&H00A0B0A8,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,1,170,170,70,1\n"
            f"Style: Shape,Arial,20,{L.CHALK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def _ms(text: str) -> int:
    return max(500, min(2400, 42 * len(L._plain(text))))


def _write(ev: List[str], layer: int, a: float, b: float, style: str, x: int, y: int, text: str, size: int,
           rng: random.Random) -> int:
    """A line written by hand from left to right at the pace of writing, a piece of chalk travelling along it and a little
    dust falling at the end. Returns the width written."""
    ms = _ms(text)
    per_char = 0.44 if re.search(r"[A-Za-z]", text) and not re.search(r"[ऀ-ॿ]", text) else 0.5
    width = min(1500, int(len(L._plain(text)) * size * per_char) + 30)     # the camera drift crops the outer edges
    ev.append(L._ev(layer, a, b, style, f"{{\\an7\\pos({x},{y})\\blur0.6\\clip({x - 6},{y - 14},{x - 5},{y + size + 50})"
                                        f"\\t(0,{ms},\\clip({x - 6},{y - 14},{x + width},{y + size + 50}))\\fad(0,450)}}{L._ass(text)}"))
    yb = y + int(size * 0.8)
    ev.append(L._ev(layer + 2, a, a + ms / 1000 + 0.35, "Shape",
                    f"{{\\an7\\move({x},{yb},{x + width - 20},{yb - 6},0,{ms})\\frz28\\c&H00F4F6F6&\\blur0.8\\fad(120,300)\\p1}}"
                    f"{L.CHALK_STICK}{{\\p0}}"))
    t = a + ms / 1000
    for _ in range(4):
        dx, dy, r = rng.randint(-30, 20), rng.randint(40, 110), rng.randint(2, 4)
        ev.append(L._ev(layer + 1, t, t + 0.9, "Shape", f"{{\\an7\\move({x + width - 20},{yb},{x + width - 20 + dx},{yb + dy},0,850)"
                                                        f"\\1a&H60&\\blur1.5\\fad(0,600)\\p1}}{L._blob(r)}{{\\p0}}"))
    return width


def _underline(ev: List[str], a: float, b: float, x: int, y: int, width: int, rng: random.Random, tick: bool) -> None:
    ev.append(L._ev(2, a, b, "Shape", f"{{\\an7\\pos({x},{y})\\c{L.YELLOW}\\blur0.8\\clip({x},{y - 20},{x + 1},{y + 30})"
                                      f"\\t(0,450,\\clip({x},{y - 20},{x + width + 10},{y + 30}))\\fad(0,450)\\p1}}{L._stroke(rng, width, 6)}{{\\p0}}"))
    if tick:
        ev.append(L._ev(2, a + 0.5, b, "Shape", f"{{\\an7\\pos({x + width + 26},{y - 58})\\c{L.YELLOW}\\blur0.8\\fscx34\\fscy34"
                                                f"\\t(0,180,\\fscx70\\fscy70)\\fad(0,450)\\p1}}{L.TICK}{{\\p0}}"))


def _at(narration: str, cue: str, a: float, b: float) -> float:
    """When a board line is written: as the teacher reaches its words (by their place in the narration)."""
    pos = narration.lower().find((cue or "").lower())
    if pos < 0:
        return -1.0
    return a + (b - a) * pos / max(1, len(narration))


def make_lecture(lec: Dict[str, Any], code: str = "hi", progress: Callable[[int, str], None] = lambda p, m: None) -> Dict[str, Any]:
    lang = yt.LANGS[code]
    preset = yt_voice.DEFAULT[code]
    cast = yt_voice.CAST[lec.get("cast", "lectures")][code]          # maths lectures keep the maths teachers' voices
    parts = [{"head": "", "narration": lec["intro"][code], "board": []}] + lec["sections"][code] + [lec["closing"][code]]
    pid = f"lectures_{code}_{secrets.token_hex(4)}"
    out_dir, work = yt.pack_dir(pid), TEMP_DIR / "youtube_work" / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        files, engine = yt_voice.voice_parts([p["narration"] for p in parts], preset, work, teacher=True,
                                             progress=lambda m: progress(30, m), cast=cast)
        durs = [yt_voice.to_wav(f, work / f"p{i}.wav") for i, f in enumerate(files)]
        times, t = [], 1.4
        for i, d in enumerate(durs):
            if i:
                t += 1.8                                   # the board is wiped and the next heading goes up
            times.append((t, t + d))
            t += d
        total = t + 1.6
        with wave.open(str(work / "p0.wav"), "rb") as w0:
            rate, width = w0.getframerate(), w0.getsampwidth()
        audio = bytearray(int(total * rate) * width)
        for i, (a, _) in enumerate(times):
            yt._place(audio, work / f"p{i}.wav", a, rate, width)
        with wave.open(str(work / "narration.wav"), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(width)
            wv.setframerate(rate)
            wv.writeframes(bytes(audio))

        rng = random.Random(len(lec["title"][code]) * 31 + len(code))
        ev: List[str] = []
        for _ in range(7):                                 # faint smudges of old chalk
            r = rng.randint(100, 240)
            ev.append(L._ev(0, 0, total, "Shape", f"{{\\an7\\pos({rng.randint(0, W)},{rng.randint(0, H)})\\1a&HF0&\\blur40\\p1}}{L._blob(r)}{{\\p0}}"))
        ev.append(L._ev(1, 0, total, "Kicker", f"{{\\pos({X0},86)\\fad(500,0)}}{L._ass(lec['kicker'][code])}"))
        ev.append(L._ev(1, 0.6, times[1][0] + 8, "Foot", f"{{\\fad(600,600)}}{L._ass(lec['credit_short'][code])}"))
        # the opening: the title written large while the teacher introduces the lesson
        a0, b0 = times[0]
        _write(ev, 1, a0 + 0.3, b0 + 0.8, "Title", X0, 420, lec["title"][code], 92, rng)
        for i, p in enumerate(parts[1:], 1):
            a, b = times[i]
            end = b + 0.9
            hw = _write(ev, 1, a - 0.2, end, "Head", X0, 118, p["head"], 72, rng)
            _underline(ev, a + _ms(p["head"]) / 1000, end, X0, 216, hw, rng, False)
            lines = p.get("board") or []
            y = 296
            prev = a + _ms(p["head"]) / 1000 + 0.3
            for k, line in enumerate(lines):
                at = _at(p["narration"], line.get("at", ""), a, b)
                if at < prev:
                    at = prev if at < 0 or at < prev else at
                if at < 0:
                    at = a + (b - a) * (k + 1) / (len(lines) + 1)
                wdt = _write(ev, 1, at, end, "Point", X0 + 20, y, line["text"], 56, rng)
                if line.get("key"):
                    _underline(ev, at + _ms(line["text"]) / 1000 + 0.1, end, X0 + 20, y + 88, wdt - 20, rng, True)
                prev = at + _ms(line["text"]) / 1000 + 0.2
                y += 140
            # the duster: a soft sweep across the board as it is wiped for the next part
            ev.append(L._ev(4, end - 0.3, end + 0.9, "Shape", f"{{\\an7\\move(-700,-40,{W + 200},-40,0,1100)\\c&H002E3526&\\1a&H30&\\blur70"
                                                            f"\\fad(150,250)\\p1}}m 0 0 l 520 0 520 {H + 80} 0 {H + 80}{{\\p0}}"))
        (work / "lecture.ass").write_text(_head(lang) + "\n".join(ev) + "\n", encoding="utf-8")
        # the board, a little larger than the frame, so the camera can drift very slowly across it
        subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x26352e:s=1920x1080:d=1", "-vf",
                        f"noise=alls=16:allf=u:all_seed={rng.randint(1, 99999)},gblur=sigma=1.4,eq=contrast=1.05,vignette=PI/4.2",
                        "-frames:v", "1", str(work / "board.png")], check=True)
        video = out_dir / "video.mp4"
        progress(85, "Rendering...")
        yt._run([FFMPEG_BIN, "-y", "-v", "error", "-loop", "1", "-framerate", "24", "-i", "board.png", "-i", "narration.wav",
                 "-filter_complex", "[0:v]ass=lecture.ass,scale=2112:1188,crop=1920:1080:x='96+46*sin(t/11)':y='54+24*sin(t/17)',"
                                    "format=yuv420p[v];"
                                    "[1:a]loudnorm=I=-16:TP=-1.5:LRA=9[a]",
                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tune", "stillimage",
                 "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", f"{total:.2f}", "-movflags", "+faststart", str(video.resolve())], work)
        thumb_at = times[min(3, len(times) - 1)][1] - 0.5
        subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-ss", f"{thumb_at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2",
                        str(out_dir / "thumb.jpg")], capture_output=True)
        endcard.append_end_card(str(video), lec["credit"], lec["title"][code], lang, 4.0)
        (out_dir / "transcript.txt").write_text("\n\n".join(p["narration"].strip() for p in parts) + "\n", encoding="utf-8")
        probe = endcard._probe(str(video))
        marks = ["0:00 " + lec["title"][code]] + [f"{yt._mmss(a - 0.2)} {p['head']}" for p, (a, _) in zip(parts[1:], times[1:])]
        desc = [lec["description"][code], ""] + marks + ["", lec["answers"][code], "", lec["credit"], lec["changes"][code], "",
                                                          lec["note"][code], "", lec["hashtags"][code]]
        pack = {"id": pid, "series": "lectures", "kind": "long", "entry_id": lec["id"], "language": code,
                "title": lec["yt_title"][code][:100], "description": "\n".join(desc), "tags": lec["tags"][code][:15],
                "category_id": "27", "made_for_kids": False, "altered_or_synthetic": not engine.startswith("edge"),
                "files": {"video": "video.mp4", "thumb": "thumb.jpg", "transcript": "transcript.txt"},
                "seconds": round(probe.get("duration") or 0, 1), "status": "draft", "created": time.time(),
                "voice": {"preset": preset, "engine": engine, "cast": [cast[0]], "music": False},
                "checks": {"license": lec.get("license", "CC BY"), "board_lines": sum(len(p.get("board") or []) for p in parts),
                           "audio": bool(probe.get("audio")), "size_mb": round(video.stat().st_size / 1e6, 1)}}
        return yt._save(pack)
    except Exception:
        traceback.print_exc()
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load(lecture_id: str) -> Dict[str, Any]:
    return json.loads((LECTURES / f"{lecture_id}.json").read_text(encoding="utf-8"))
