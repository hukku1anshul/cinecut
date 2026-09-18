"""
Stories, read aloud: a public-domain short story read in full, the way a good audiobook narrator reads it, over moving
scenes (an AI picture for each moment, with a 3D camera move; see scenes.py and parallax.py) when the story has a scene
plan, else over slow moves across public-domain paintings; with a soft music bed, a title card, a card for each part of
the story and a few of its lines on screen while they are spoken.

An English video reads the public-domain English text exactly as printed; a Hindi video reads a Hindi translation of it
that has been checked line by line (its description says so). A story is prepared as a JSON file in
output/youtube/_stories/<id>.json: titles, the spoken introduction, the parts (title, text, one line to show) in each
language, the credit line and the painting hint. The text is split into parts at paragraph breaks (parts_of).
"""
import json
import re
import secrets
import shutil
import time
import traceback
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.config import OUTPUT_DIR, TEMP_DIR
from backend.video_engine import endcard
from backend.webapp import parallax, scenes as sc, youtube as yt, yt_voice

STORIES = OUTPUT_DIR / "youtube" / "_stories"
SERIES = {"hi": "कहानी सुनिए", "en": "Stories, Read Aloud"}
VOICE = {"hi": ("hi", "Charon"), "en": ("en-gb", "Umbriel")}     # (accent and style preset, the narrator's voice)
WORDS = {"hi": {"parts": "भाग", "opening": "आरंभ", "story": "कहानी", "translation": "अनुवाद", "paintings": "चित्र",
                "note": "कहानी का पाठ AI वाचक स्वर में है; चित्र और संगीत सार्वजनिक (public-domain) या स्वयं बनाए गए हैं।",
                "tags": "#कहानी #हिंदीकहानी #ऑडियोबुक #कहानीसुनिए"},
         "en": {"parts": "Parts", "opening": "Opening", "story": "Story", "translation": "Translation", "paintings": "Paintings",
                "note": "Read by an AI narrator voice; the paintings and music are public domain or made for this video.",
                "tags": "#Audiobook #ShortStory #ClassicLiterature #StoriesReadAloud"}}
SCENE_NOTE = {"hi": "कहानी का पाठ AI वाचक स्वर में है; दृश्य इस वीडियो के लिए बनाए गए AI चित्र हैं, और संगीत भी इसी के लिए बनाया गया है।",
              "en": "Read by an AI narrator voice; the scenes are AI pictures made for this video, and so is the music."}


def _scene_shots(story: Dict[str, Any], chapters: List[Dict[str, Any]], times: List[Tuple[float, float]], bounds: List[float],
                 pics: List[List[Path]], code: str) -> List[Tuple[Path, float, float, str]]:
    """Each chapter's scenes laid over its span: a scene starts where its phrase falls in the text (by its share of the
    characters), else the chapter is shared evenly; at least 4 seconds per scene."""
    plan = [story["scenes"].get("intro", [])] + list(story["scenes"].get("parts", []))
    shots: List[Tuple[Path, float, float, str]] = []
    for i, ch in enumerate(chapters):
        s0, s1 = bounds[i], bounds[i + 1]
        items = plan[i] if i < len(plan) else []
        if not items:
            if shots:                                      # no scenes for this chapter: the last scene carries on
                p, a, _, mv = shots[-1]
                shots[-1] = (p, a, s1, mv)
            continue
        text, (a, b) = ch["narration"], times[i]
        # cued scenes start where their phrase falls; the others share the time between the cued ones around them
        known: List[Optional[float]] = []
        for k, item in enumerate(items):
            at = (item.get("at") or {}).get(code, "")
            pos = text.find(at) if at else -1
            known.append(s0 if k == 0 else (a + (b - a) * pos / max(1, len(text)) - 0.3 if pos >= 0 else None))
        known.append(s1)
        k = 1
        while k < len(items):
            if known[k] is None:
                j = k
                while known[j] is None:
                    j += 1
                lo, hi = known[k - 1], known[j]
                for m in range(k, j):
                    known[m] = lo + (hi - lo) * (m - k + 1) / (j - k + 1)
                k = j
            k += 1
        starts: List[float] = []
        for k in range(len(items)):
            starts.append(s0 if k == 0 else max(starts[-1] + 4.0, min(known[k], s1 - 4.0)))
        for k, item in enumerate(items):
            shots.append((pics[i][k], starts[k], starts[k + 1] if k + 1 < len(items) else s1, item.get("move")))
    return shots


def parts_of(text: str, target: int = 420) -> List[str]:
    """Paragraphs grouped into parts of about `target` words, never splitting a paragraph; a short tail joins the last part."""
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: List[str] = []
    cur: List[str] = []
    n = 0
    for p in paras:
        w = len(p.split())
        if cur and n + w > target:
            out.append("\n\n".join(cur))
            cur, n = [], 0
        cur.append(p)
        n += w
    if cur:
        if out and n < target * 0.35:
            out[-1] += "\n\n" + "\n\n".join(cur)
        else:
            out.append("\n\n".join(cur))
    return out


def load(story_id: str) -> Dict[str, Any]:
    return json.loads((STORIES / f"{story_id}.json").read_text(encoding="utf-8"))


def make_reading(story: Dict[str, Any], code: str = "hi", progress: Callable[[int, str], None] = lambda p, m: None) -> Dict[str, Any]:
    lang, w = yt.LANGS[code], WORDS[code]
    preset, narrator = VOICE[code]
    parts = story["parts"][code]
    chapters = [{"title": "", "narration": story["intro"][code], "quote": ""}]
    chapters += [{"title": p["title"], "narration": p["text"], "quote": p.get("quote", "")} for p in parts]
    doc = {"title": story["title"][code], "chapters": chapters}
    pid = f"stories_{code}_{secrets.token_hex(4)}"
    out_dir, work = yt.pack_dir(pid), TEMP_DIR / "youtube_work" / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        # the scene pictures first, so a used-up picture allowance stops the story before any voice is spent
        pics: List[List[Path]] = []
        if story.get("scenes"):
            plan = [story["scenes"].get("intro", [])] + list(story["scenes"].get("parts", []))
            pics = [[sc.picture(it.get("id") or f"{story['id']}-{i}-{k}", it["prompt"], it.get("seed", 7)) for k, it in enumerate(items)]
                    for i, items in enumerate(plan)]
        # read as a performance: the audiobook style ("story"), one narrator's voice from start to end
        files, engine = yt_voice.voice_parts([c["narration"] for c in chapters], preset, work, teacher="story",
                                             progress=lambda m: progress(30, m), cast=(narrator, None))
        durs = [yt_voice.to_wav(f, work / f"ch{i}.wav") for i, f in enumerate(files)]
        times, t = [], 1.2
        for i, d in enumerate(durs):
            if i == 1:
                t += 4.6                                   # the title card
            elif i > 1:
                t += 2.4                                   # a breath between parts, under the part's card
            times.append((t, t + d))
            t += d
        speech_end = t + 1.4
        total = speech_end + 4.0
        with wave.open(str(work / "ch0.wav"), "rb") as w0:
            rate, width = w0.getframerate(), w0.getsampwidth()
        audio = bytearray(int(total * rate) * width)
        for i, (a, _) in enumerate(times):
            yt._place(audio, work / f"ch{i}.wav", a, rate, width)
        with wave.open(str(work / "narration.wav"), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(width)
            wv.setframerate(rate)
            wv.writeframes(bytes(audio))
        (work / "doc.ass").write_text(yt._doc_ass(doc, times, total, speech_end, lang, SERIES[code], story["credit"][code]),
                                      encoding="utf-8")
        bounds = [0.0] + [a - 1.9 for a, _ in times[1:]] + [total]
        if story.get("scenes"):
            progress(60, "Filming the scenes...")
            shots = _scene_shots(story, chapters, times, bounds, pics, code)
            bg = parallax.render_sequence(shots, total, work / "bg.mp4")
            art_credits = []
            first_image = next((s[0] for s in shots if Path(s[0]).suffix.lower() not in parallax.VIDEO_EXT), None)
        else:
            progress(60, "Filming the paintings...")
            bg, art_credits, first_image = yt._background("classics", story["hint"], bounds, work, story.get("subjects"), story.get("avoid"))
        video = out_dir / "video.mp4"
        progress(85, "Rendering...")
        yt._render(work, bg, "doc.ass", "stories", total, video)
        endcard.append_end_card(str(video), story["endcard"][code], doc["title"], lang, 4.0)
        yt._thumbnail(doc["title"], SERIES[code], out_dir / "thumb.jpg", work, first_image, lang)
        (out_dir / "transcript.txt").write_text("\n\n".join(c["narration"].strip() for c in chapters) + "\n", encoding="utf-8")
        probe = endcard._probe(str(video))
        marks = [f"0:00 {w['opening']}"] + [f"{yt._mmss(a - 1.9)} {c['title']}" for c, (a, _) in zip(chapters[1:], times[1:])]
        desc = [story["description"][code], "", f"{w['parts']}:"] + marks + ["", story["credit"][code]]
        if art_credits:
            desc += ["", f"{w['paintings']}:"] + [f"• {c}" for c in art_credits]
        desc += ["", f"{w['story']}: {story['source_url']}", SCENE_NOTE[code] if story.get("scenes") else w["note"], "", w["tags"]]
        pack = {"id": pid, "series": "stories", "kind": "long", "entry_id": story["id"], "language": code,
                "title": story["yt_title"][code][:100], "description": "\n".join(desc), "tags": story["tags"][code][:15],
                "category_id": "24", "made_for_kids": False, "altered_or_synthetic": not engine.startswith("edge"),
                "files": {"video": "video.mp4", "thumb": "thumb.jpg", "transcript": "transcript.txt"},
                "seconds": round(probe.get("duration") or 0, 1), "status": "draft", "created": time.time(),
                "voice": {"preset": preset, "engine": engine, "cast": [narrator], "music": True},
                "checks": {"cleared_in": story.get("cleared_in", []), "text": story["text_note"][code], "parts": len(parts),
                           "paintings": len(art_credits), "scenes": sum(len(x) for x in pics), "audio": bool(probe.get("audio")),
                           "size_mb": round(video.stat().st_size / 1e6, 1)}}
        return yt._save(pack)
    except Exception:
        traceback.print_exc()
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
