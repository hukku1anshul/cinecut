"""
The Vedas, Explained (docs/vedas_course.md): lessons in the lecture format. A teacher's voice explains; key words are written
up in ink on a manuscript-coloured board as they are said (a Sanskrit term in Devanagari, its transliteration, its meaning);
a verse is shown in Devanagari with its transliteration and recited in Sanskrit by the teacher before it is explained;
the voices and the speaking style are the lectures' own (the viewer preferred them); a soft tanpura plays underneath.
Every lesson puts its weight on learnings for real life: an everyday situation, and one small thing to try.
A lesson is prepared as output/youtube/_course/<id>.json: parts of type "intro", "section", "verse" and "closing".
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
from backend.webapp import artwork, lecture as LC, lessons as L, music, parallax, sanskrit, scenes, storyteller, youtube as yt, yt_voice

COURSE = OUTPUT_DIR / "youtube" / "_course"
W, H = 1920, 1080
X0 = 170
INK = "&H001C2632"          # near-black brown ink
RED = "&H001E2A8C"          # deep red for the key line and the headings
GOLD = "&H00204E7A"         # dark ochre for the transliteration
HAND = {"English": "Segoe Print", "Hindi": "Nirmala UI"}
SERIES = {"hi": "वेद, सरल भाषा में", "en": "The Vedas, Explained"}
CAST = {"hi": ("Achird", None), "en": ("Sadaltager", None)}      # the lecture teachers' voices; the teacher recites the verse too
STYLES = LC.STYLES


def _head(lang: str) -> str:
    hand = HAND[lang]
    sp = 2 if lang == "English" else 0
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n" + STYLES +
            f"Style: Kicker,Nirmala UI,30,{RED},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,{sp},0,1,0,0,7,0,0,0,1\n"
            f"Style: Head,{hand},72,{RED},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Point,{hand},58,{INK},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Title,{hand},92,{INK},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Deva,Nirmala UI,66,{INK},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Iast,Cambria,42,{GOLD},&H000000FF,&H00000000,&H00000000,1,1,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Gloss,{'Georgia' if lang == 'English' else 'Nirmala UI'},40,{INK},&H000000FF,&H00000000,&H00000000,0,"
            f"{1 if lang == 'English' else 0},0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            f"Style: Foot,Nirmala UI,24,&H00607080,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,1,170,170,70,1\n"
            f"Style: SKick,Nirmala UI,28,&H00F4F4F4,&H000000FF,&H00000000,&H90000000,1,0,0,0,100,100,{sp},0,1,1,2,7,0,0,0,1\n"
            f"Style: SHead,{'Segoe UI Semibold' if lang == 'English' else 'Nirmala UI'},66,&H00FFFFFF,&H000000FF,&H00101010,&H90000000,1,0,0,0,100,100,0,0,1,2,4,7,0,0,0,1\n"
            f"Style: STitle,{'Georgia' if lang == 'English' else 'Nirmala UI'},100,&H00FFFFFF,&H000000FF,&H00101010,&H90000000,1,0,0,0,100,100,0,0,1,3,5,7,0,0,0,1\n"
            f"Style: Note,{hand},64,{INK},&H000000FF,&H0CD2E2EC,&H00000000,1,0,0,0,100,100,0,0,3,22,0,7,0,0,0,1\n"
            f"Style: Shape,Arial,20,{INK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def _plain_len(text: str) -> int:
    return len(re.sub(r"\{[^}]*\}", "", text))


def _write(ev: List[str], layer: int, a: float, b: float, style: str, x: int, y: int, shown: str, size: int, plain_len: int) -> int:
    """A line written in ink from left to right at the pace of writing, a pen moving along it. `shown` may carry font tags."""
    ms = max(500, min(2400, 42 * plain_len))
    width = min(1560, int(plain_len * size * 0.47) + 30)
    ev.append(L._ev(layer, a, b, style, f"{{\\an7\\pos({x},{y})\\blur0.5\\clip({x - 6},{y - 14},{x - 5},{y + size + 50})"
                                        f"\\t(0,{ms},\\clip({x - 6},{y - 14},{x + width},{y + size + 50}))\\fad(0,450)}}{shown}"))
    yb = y + int(size * 0.8)
    ev.append(L._ev(layer + 2, a, a + ms / 1000 + 0.3, "Shape",
                    f"{{\\an7\\move({x},{yb},{x + width - 20},{yb - 6},0,{ms})\\frz32\\c{INK}\\blur0.6\\fad(100,250)\\p1}}"
                    f"m 0 0 l 58 0 60 3 60 9 58 12 0 12 -8 6{{\\p0}}"))
    return width


def _line(ev: List[str], a: float, b: float, x: int, y: int, item: Dict[str, Any], lang: str) -> int:
    """One board line: plain text, a Devanagari term before its text ("deva"), or two columns ("cols")."""
    hand = HAND[lang]
    if item.get("cols"):
        left, right = item["cols"]
        _write(ev, 1, a, b, "Point", x, y, f"{{\\c{RED}\\b1}}{L._ass(left)}", 58, len(left))
        return 520 + _write(ev, 1, a + 0.6, b, "Point", x + 520, y, L._ass(right), 58, len(right))
    if item.get("deva"):
        shown = f"{{\\fnNirmala UI\\b1}}{L._ass(item['deva'])}{{\\fn{hand}\\b0}}   {L._ass(item['text'])}"
        return _write(ev, 1, a, b, "Point", x, y, shown, 58, len(item["deva"]) + 3 + len(item["text"]))
    return _write(ev, 1, a, b, "Point", x, y, L._ass(item["text"]), 58, len(item["text"]))


def _scene_part(ev: List[str], p: Dict[str, Any], a: float, b: float, start: float, end: float,
                lesson: Dict[str, Any], code: str, lang: str) -> None:
    """A story part over full-screen scenes: the course name small at the top, the part's heading in white (for the opening,
    the lesson title, after the cold open), and the board lines as handwritten notes on a paper strip along the bottom,
    one at a time, each as it is said; a soft shade at the bottom keeps the notes readable on any picture."""
    ev.append(L._ev(0, start - 0.5, end, "Shape", "{\\an7\\pos(0,690)\\c&H000000&\\1a&H7A&\\blur90\\p1}m 0 0 l 1920 0 1920 430 0 430{\\p0}"))
    ev.append(L._ev(1, start - 0.5, end, "SKick", f"{{\\pos({X0},86)\\fad(400,300)}}{L._ass(lesson['kicker'][code])}"))
    if p.get("type") == "intro":
        t0 = LC._at(p["narration"], p.get("title_at", ""), a, b) if p.get("title_at") else -1.0
        t0 = t0 if t0 >= 0 else a + 1.0
        ev.append(L._ev(2, t0, min(end, t0 + 6.5), "STitle", f"{{\\an1\\pos({X0},640)\\fad(900,900)\\blur0.6}}{L._ass(lesson['title'][code])}"))
    elif p.get("head"):
        ev.append(L._ev(2, start + 0.2, start + 4.4, "SHead", f"{{\\an1\\pos({X0},250)\\fad(600,700)}}{L._ass(p['head'])}"))
    lines = p.get("board") or []
    ats: List[float] = []
    for k, item in enumerate(lines):
        at = LC._at(p["narration"], item.get("at", ""), a, b)
        if at < 0:
            at = a + (b - a) * (k + 1) / (len(lines) + 1)
        ats.append(max(at, ats[-1] + 1.2 if ats else start + 1.0))
    for k, item in enumerate(lines):
        until = ats[k + 1] - 0.1 if k + 1 < len(lines) else end
        text = L._ass(item.get("text") or "  ·  ".join(item.get("cols", [])))
        if item.get("deva"):
            text = f"{{\\fnNirmala UI\\b1}}{L._ass(item['deva'])}{{\\fn{HAND[lang]}\\b0}}   " + text
        colour = f"\\c{RED}" if item.get("key") else ""
        ev.append(L._ev(3, ats[k], until, "Note", f"{{\\an1\\pos({X0},960)\\fad(350,250){colour}}}{text}"))


def make_lesson(lesson: Dict[str, Any], code: str = "hi", progress: Callable[[int, str], None] = lambda p, m: None) -> Dict[str, Any]:
    lang = yt.LANGS[code]
    preset = yt_voice.DEFAULT[code]
    cast = CAST[code]
    parts = lesson["parts"][code]
    pid = f"vedas_{code}_{secrets.token_hex(4)}"
    out_dir, work = yt.pack_dir(pid), TEMP_DIR / "youtube_work" / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        # the verses, recited in Sanskrit by the teacher (kept, so each verse is recited once for both languages)
        cards = {}
        for i, p in enumerate(parts):
            if p.get("type") == "verse":
                v = p["verse"]
                card = sanskrit.mantra_card(v["book"], v["hymn"], v.get("verse", 1))
                if not card:
                    raise RuntimeError(f"no Sanskrit text for {v}")
                reciter = yt_voice.RECITER             # Sanskrit in a Hindi tone, in both languages
                got = yt_voice.recite_sanskrit(card["lines"], reciter,
                                               storyteller.CACHE / f"sanskrit_rv_{card['ref'].replace('.', '_')}_{reciter}.mp3", work)
                if not got:
                    raise RuntimeError("no voice could read the Sanskrit verse yet (daily quota)")
                rec = work / f"verse{i}.wav"
                shutil.copy(got[0], rec)
                cards[i] = (card, rec, got[1])
        # the scene pictures first, so a used-up picture allowance stops the lesson before any voice is spent
        pics = {}
        for i, p in enumerate(parts):
            for k, s in enumerate(p.get("scenes") or []):
                pics[(i, k)] = scenes.picture(s.get("id") or f"{lesson['id']}-{i}-{k}", s["prompt"], s.get("seed", 7),
                                              also=[s["picture"]] if s.get("picture") else None)
        files, engine = yt_voice.voice_parts([p["narration"] for p in parts], preset, work, teacher="course",
                                             progress=lambda m: progress(30, m), cast=cast)
        durs = [yt_voice.to_wav(f, work / f"p{i}.wav") for i, f in enumerate(files)]
        times, recs, t = [], {}, 1.6
        for i, d in enumerate(durs):
            if i:
                t += 1.8                                           # the board is cleared for the next part
            if i in cards:
                recs[i] = (t, t + cards[i][2])
                t += cards[i][2] + 0.9
            times.append((t, t + d))
            t += d
        total = t + 1.8
        with wave.open(str(work / "p0.wav"), "rb") as w0:
            rate, width = w0.getframerate(), w0.getsampwidth()
        audio = bytearray(int(total * rate) * width)
        for i, (a, _) in enumerate(times):
            yt._place(audio, work / f"p{i}.wav", a, rate, width)
        for i, (a, _) in recs.items():
            yt._place(audio, cards[i][1], a, rate, width)
        with wave.open(str(work / "narration.wav"), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(width)
            wv.setframerate(rate)
            wv.writeframes(bytes(audio))

        rng = random.Random(len(lesson["title"][code]) * 17 + len(code))
        ev: List[str] = []
        ev.append(L._ev(1, 0, total, "Kicker", f"{{\\pos({X0},86)\\fad(500,0)}}{L._ass(lesson['kicker'][code])}"))
        for i, p in enumerate(parts):
            a, b = times[i]
            start = recs[i][0] if i in recs else a
            end = b + 1.0
            if p.get("scenes"):                                     # a story part: full-screen scenes, notes along the bottom
                _scene_part(ev, p, a, b, start, end, lesson, code, lang)
                continue
            ev.append(L._ev(1, start - 0.5, end, "Kicker", f"{{\\pos({X0},86)\\fad(400,300)}}{L._ass(lesson['kicker'][code])}"))
            if p.get("type") == "intro":
                _write(ev, 1, a + 0.3, end, "Title", X0, 400, L._ass(lesson["title"][code]), 92, len(lesson["title"][code]))
                continue
            hw = _write(ev, 1, start - 0.2, end, "Head", X0, 150, L._ass(p["head"]), 70, len(p["head"]))
            ev.append(L._ev(2, start + 0.6, end, "Shape", f"{{\\an7\\pos({X0},{246})\\c{RED}\\blur0.7\\clip({X0},226,{X0 + 1},276)"
                                                           f"\\t(0,450,\\clip({X0},226,{X0 + hw + 10},276))\\fad(0,450)\\p1}}"
                                                           f"{L._stroke(rng, hw, 5)}{{\\p0}}"))
            y = 316
            if i in cards:                                          # the verse: Devanagari as it is recited, then its transliteration
                card, _, rd = cards[i]
                ra, rb = recs[i]
                for k, ln in enumerate(card["lines"]):
                    la = ra + rd * k / max(1, len(card["lines"]))
                    _write(ev, 1, la, end, "Deva", X0 + 20, y, L._ass(ln), 66, len(ln))
                    y += 92
                y += 10
                for ln in ([] if lesson.get("plain") else card["lines"]):     # a plain lesson: no transliteration
                    ev.append(L._ev(1, rb - 0.4, end, "Iast", f"{{\\an7\\pos({X0 + 24},{y})\\fad(500,400)}}{L._ass(sanskrit.iast(ln))}"))
                    y += 52
                if p.get("gloss"):
                    ev.append(L._ev(1, a + 0.4, end, "Gloss", f"{{\\an7\\pos({X0 + 24},{y + 24})\\fad(600,400)}}{L._ass(p['gloss'])}"))
                    y += 64
                # the hymn's three facts, as every lesson shows them: seer, deity, metre
                facts = []
                for en_label, hi_label, key in (("Seer", "ऋषि", "rishi"), ("Deity", "देवता", "devata"), ("Metre", "छन्द", "chhanda")):
                    if card.get(key):
                        facts.append(f"{en_label}: {sanskrit.iast(card[key]).title()}" if lang == "English" else f"{hi_label}: {card[key]}")
                label = f"{lesson['sanskrit_label'][code]} {card['ref'] if lang == 'English' else card['ref'].translate(sanskrit.DIGITS)}"
                font = "Cambria" if lang == "English" else "Nirmala UI"
                if lesson.get("plain"):                                  # the source reference only, no metre facts
                    facts = []
                ev.append(L._ev(1, ra + 0.5, end, "Gloss", f"{{\\an7\\pos({X0 + 24},{y + 40})\\fn{font}\\i0\\b1\\c{RED}\\fs36\\fad(600,400)}}"
                                                          f"{L._ass('   ·   '.join([label] + facts))}"))
            lines = p.get("board") or []
            prev = start + 0.9
            for k, item in enumerate(lines):
                at = LC._at(p["narration"], item.get("at", ""), a, b)
                if at < 0:
                    at = a + (b - a) * (k + 1) / (len(lines) + 1)
                at = max(at, prev)
                wdt = _line(ev, at, end, X0 + 20, y, item, lang)
                if item.get("key"):
                    ev.append(L._ev(2, at + 1.2, end, "Shape", f"{{\\an7\\pos({X0 + 20},{y + 86})\\c{RED}\\blur0.7\\clip({X0 + 20},{y + 66},"
                                                               f"{X0 + 21},{y + 110})\\t(0,450,\\clip({X0 + 20},{y + 66},{X0 + 30 + wdt},{y + 110}))"
                                                               f"\\fad(0,450)\\p1}}{L._stroke(rng, max(60, wdt - 20), 5)}{{\\p0}}"))
                prev = at + 1.1
                y += 120
            # turning to a fresh page: a soft light sweep as the board is cleared
            ev.append(L._ev(4, end - 0.3, end + 0.9, "Shape", f"{{\\an7\\move(-700,-40,{W + 200},-40,0,1100)\\c&H00C8DCE8&\\1a&H40&\\blur70"
                                                            f"\\fad(150,250)\\p1}}m 0 0 l 520 0 520 {H + 80} 0 {H + 80}{{\\p0}}"))
        (work / "lesson.ass").write_text(_head(lang) + "\n".join(ev) + "\n", encoding="utf-8")
        # public-domain pictures pinned to the board in a paper mount: a large one on the right, or a small one in the top corner
        pins, credits = [], []
        for i, p in enumerate(parts):
            want = p.get("image")
            if not want or p.get("scenes"):                        # pictures are pinned to the board only
                continue
            try:
                img = next((x for x in artwork.search(want["q"], 15) if want["match"].lower() in x["title"].lower()), None)
                if not img:
                    continue
                path = artwork.fetch(img)
            except Exception:
                continue
            a = (recs[i][0] if i in recs else times[i][0]) + 0.8
            # kept inside x 1740: the camera drift crops up to about 7% of each edge
            box = (1390, 110, 350, 226) if want.get("box") == "corner" else (1150, 300, 590, 450)
            pins.append((path, a, times[i][1] + 0.9, box))
            if artwork.credit(img) not in credits:
                credits.append(artwork.credit(img))
        subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0xE8DCC2:s=1920x1080:d=1", "-vf",
                        f"noise=alls=10:allf=u:all_seed={rng.randint(1, 99999)},gblur=sigma=1.2,vignette=PI/4.6",
                        "-frames:v", "1", str(work / "board.png")], check=True)
        # the moving picture under the lesson: the story parts are scenes (a 3D camera move over each picture), the teaching
        # parts the board; one pass renders them all, with a dissolve at each change
        def part_start(i: int) -> float:
            return 0.0 if i == 0 else (recs[i][0] if i in recs else times[i][0]) - 0.9
        shots = []
        for i, p in enumerate(parts):
            s0, s1 = part_start(i), (part_start(i + 1) if i + 1 < len(parts) else total)
            if not p.get("scenes"):
                shots.append((work / "board.png", s0, s1, "still"))
                continue
            a, b = times[i]
            starts: List[float] = []
            for k, s in enumerate(p["scenes"]):
                at = LC._at(p["narration"], s.get("at", ""), a, b) if s.get("at") else -1.0
                even = a + (b - a) * s["frac"] if "frac" in s else s0 + (s1 - s0) * k / len(p["scenes"])   # its share of the text
                starts.append(s0 if k == 0 else max(starts[-1] + 2.5, at - 0.3 if at >= 0 else even))
            for k, s in enumerate(p["scenes"]):
                shots.append((pics[(i, k)], starts[k], starts[k + 1] if k + 1 < len(starts) else s1, s.get("move")))
        progress(70, "Filming the scenes...")
        parallax.render_sequence(shots, total, work / "bg.mp4")
        # the voice with a soft tanpura under it, ducked while the teacher speaks
        loop = music.bed("vedas")
        voice_l, bed_l = yt._lufs(work / "narration.wav"), yt._lufs(loop)
        gain = (voice_l - 20 - bed_l) if voice_l is not None and bed_l is not None else -24
        fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        # each picture is scaled and mounted once, here, so the video filter only overlays a small ready image
        mounted = []
        for k, (path, a, b, (bx, by, bw, bh)) in enumerate(pins):
            m = work / f"pin{k}.png"
            subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-i", str(path), "-vf",
                            f"scale={bw - 32}:{bh - 32}:force_original_aspect_ratio=decrease,pad=iw+8:ih+8:4:4:color=0x3A2A1C,"
                            f"pad=iw+24:ih+24:12:12:color=0xF4EEDF", "-frames:v", "1", str(m)], check=True)
            mounted.append((m, a, b, (bx, by, bw, bh)))
        pins = mounted
        vchain, last = ["[0:v]format=rgba[b0]"], "b0"
        for k, (path, a, b, (bx, by, bw, bh)) in enumerate(pins):
            vchain.append(f"[{3 + k}:v]format=rgba,fade=t=in:st={a:.2f}:d=0.7:alpha=1,"
                          f"fade=t=out:st={max(a, b - 0.7):.2f}:d=0.7:alpha=1[i{k}]")
            vchain.append(f"[{last}][i{k}]overlay=x='{bx}+({bw}-w)/2':y='{by}+({bh}-h)/2':enable='between(t,{a:.2f},{b:.2f})'[b{k + 1}]")
            last = f"b{k + 1}"
        graph = (";".join(vchain) + f";[{last}]ass=lesson.ass,scale=2112:1188,crop=1920:1080:x='96+40*sin(t/12)':y='54+20*sin(t/19)',"
                 "format=yuv420p[v];"
                 f"[1:a]{fmt},asplit=2[n][k];[2:a]{fmt},atrim=0:{total:.2f},asetpts=PTS-STARTPTS,volume={gain:.1f}dB,"
                 f"afade=t=in:d=3,afade=t=out:st={max(0.0, total - 4):.2f}:d=4[bd];"
                 "[bd][k]sidechaincompress=threshold=0.05:ratio=3:attack=40:release=700[d];"
                 "[n][d]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
        video = out_dir / "video.mp4"
        progress(85, "Rendering...")
        yt._run([FFMPEG_BIN, "-y", "-v", "error", "-i", "bg.mp4", "-i", "narration.wav",
                 "-stream_loop", "-1", "-i", str(loop),
                 *[x for path, *_ in pins for x in ("-loop", "1", "-framerate", "24", "-i", str(path))],
                 "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
                 "-ar", "48000", "-t", f"{total:.2f}", "-movflags", "+faststart", str(video.resolve())], work)
        thumb_at = times[min(3, len(times) - 1)][1] - 0.5
        subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-ss", f"{thumb_at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2",
                        str(out_dir / "thumb.jpg")], capture_output=True)
        endcard.append_end_card(str(video), lesson["credit"][code], lesson["title"][code], lang, 4.0)
        (out_dir / "transcript.txt").write_text("\n\n".join(p["narration"].strip() for p in parts) + "\n", encoding="utf-8")
        probe = endcard._probe(str(video))
        marks = [f"{yt._mmss(max(0.0, (recs[i][0] if i in recs else a) - 0.2))} {p.get('head') or lesson['title'][code]}"
                 for i, (p, (a, _)) in enumerate(zip(parts, times))]
        marks[0] = "0:00 " + marks[0].split(" ", 1)[1]
        desc = [lesson["description"][code], ""] + marks + ["", lesson["credit"][code]]
        if credits:
            desc += ["", lesson.get("pictures_label", {}).get(code, "Pictures") + ":"] + [f"• {c}" for c in credits]
        desc += ["", lesson["note"][code], "", lesson["hashtags"][code]]
        pack = {"id": pid, "series": "vedas", "kind": "long", "entry_id": lesson["id"], "language": code,
                "title": lesson["yt_title"][code][:100], "description": "\n".join(desc), "tags": lesson["tags"][code][:15],
                "category_id": "27", "made_for_kids": False, "altered_or_synthetic": not engine.startswith("edge"),
                "files": {"video": "video.mp4", "thumb": "thumb.jpg", "transcript": "transcript.txt"},
                "seconds": round(probe.get("duration") or 0, 1), "status": "draft", "created": time.time(),
                "voice": {"preset": preset, "engine": engine, "cast": list(cast), "music": True},
                "checks": {"course_lesson": lesson["number"], "verses": len(cards), "audio": bool(probe.get("audio")),
                           "size_mb": round(video.stat().st_size / 1e6, 1)}}
        return yt._save(pack)
    except Exception:
        traceback.print_exc()
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load(lesson_id: str) -> Dict[str, Any]:
    return json.loads((COURSE / f"{lesson_id}.json").read_text(encoding="utf-8"))
