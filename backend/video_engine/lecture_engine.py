"""
Lecture / educational mode.

Turns a long lecture, webinar or tutorial into a study cut:
  1. Segments the lecture into topic blocks at slide changes (shot cuts) and long pauses.
  2. Scores each block: explanation density, "key idea" cues (definitions, summaries, examples),
     distinctive vocabulary; penalizes housekeeping and tangents (audio checks, homework, breaks).
  3. Keeps the best blocks while covering the whole lecture, in order, within the time budget.
  4. Produces study notes (key points per topic, flashcards) with timestamps.
With a Gemini key, Gemini picks the segments, key points and flashcards instead.
"""
import math
import re
from bisect import bisect_left
from collections import Counter
from typing import List, Dict, Any, Optional

from backend.video_engine.probe import format_seconds
from backend.video_engine.summarizer import _snap_start, _snap_end, _resolve_overlaps, _fit_to_budget

STOPWORDS = set("""
a an the and or but if then so to of in on at by for with about as into like through over after before between
is are was were be been being am do does did done have has had i you he she it we they me him her us them my your
his its our their this that these those what which who whom whose there here when where why how all any both each
few more most other some such no nor not only own same than too very can could will would shall should may might
must just now also okay ok yeah yes um uh uhm hmm right actually basically going gonna get got getting let lets well
really thing things kind sort one two three first second next last lot lots way ways say said says see look looking
know think want make made take went come came use used using want need able sure maybe much many even still back
""".split())

KEY_CUES = [re.compile(p) for p in (
    r"\bis defined as\b", r"\bdefinition\b", r"\bwe call\b", r"\bis called\b", r"\bknown as\b", r"\bmeans that\b",
    r"\bthe key (?:idea|point|insight|thing|concept)\b", r"\bimportant\b", r"\bremember\b", r"\bin summary\b",
    r"\bto summari[sz]e\b", r"\btheorem\b", r"\bformula\b", r"\bfor example\b", r"\bfor instance\b",
    r"\bthe idea is\b", r"\bnote that\b", r"\bexam\b", r"\bin other words\b", r"\btherefore\b", r"\bequation\b",
    r"\bprove\b", r"\bdefine\b", r"\bprinciple\b", r"\blaw of\b", r"\bstep (?:one|two|three|\d)\b",
    r"\bthe main\b", r"\bessentially\b", r"\bconcept\b", r"\balgorithm\b"
)]
TANGENT_CUES = [re.compile(p) for p in (
    r"\bby the way\b", r"\banyway\b", r"\bhomework\b", r"\bdue (?:date|on|next)\b", r"\bcan (?:you|everyone) (?:all )?hear\b",
    r"\bis my screen\b", r"\bshare my screen\b", r"\btake a (?:short |quick )?break\b", r"\boffice hours\b",
    r"\bannouncements?\b", r"\bunmute\b", r"\bany questions\b", r"\bsee you next\b", r"\bgood (?:morning|afternoon)\b",
    r"\bwelcome back\b", r"\battendance\b", r"\bmidterm\b.*\broom\b", r"\bsyllabus\b", r"\bparking\b"
)]
DEFINITION_CUE_RE = re.compile(r"\b(is|are)\s+(defined as|called|known as|a|an)\s+|\b(means)\s+", re.IGNORECASE)
ARTICLES = {"a", "an", "the", "so", "and", "now", "okay", "ok", "well", "this", "that", "our", "your", "my"}


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower()) if w not in STOPWORDS]


def _make_block(subs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"start": max(0.0, subs[0]["start"] - 0.3), "end": subs[-1]["end"] + 0.3,
            "text": " ".join(s["text"] for s in subs), "subs": subs}


def segment_lecture(subtitles: List[Dict[str, Any]], shot_cuts: List[float], silences, duration: float,
                    min_block: float = 40.0, max_block: float = 240.0) -> List[Dict[str, Any]]:
    """Splits the lecture into topic blocks at slide changes and long pauses."""
    boundaries = sorted(set([round(c, 2) for c in (shot_cuts or []) if c > 0] +
                            [round((a + b) / 2, 2) for a, b in (silences or []) if b - a >= 2.0]))
    blocks: List[Dict[str, Any]] = []
    if subtitles:
        cur = [subtitles[0]]
        for sub in subtitles[1:]:
            prev = cur[-1]
            cur_len = prev["end"] - cur[0]["start"]
            i = bisect_left(boundaries, prev["end"] - 0.5)
            crossed = i < len(boundaries) and boundaries[i] <= sub["start"] + 0.5
            long_pause = sub["start"] - prev["end"] >= 2.0
            if ((crossed or long_pause) and cur_len >= min_block) or cur_len >= max_block:
                blocks.append(_make_block(cur))
                cur = [sub]
            else:
                cur.append(sub)
        blocks.append(_make_block(cur))
    else:
        start = 0.0
        for p in boundaries + [duration]:
            while p - start > max_block:
                blocks.append({"start": start, "end": start + max_block, "text": "", "subs": []})
                start += max_block
            if p - start >= min_block:
                blocks.append({"start": start, "end": p, "text": "", "subs": []})
                start = p
        if duration - start >= 10.0:
            blocks.append({"start": start, "end": duration, "text": "", "subs": []})
    return blocks


def _keywords(tf: Counter, df: Counter, n_blocks: int, k: int = 3) -> List[str]:
    scored = sorted(((c * math.log((1 + n_blocks) / (1 + df.get(w, 0))), w) for w, c in tf.items()), reverse=True)
    return [w for _, w in scored[:k]]


def _score_block(block: Dict[str, Any], tf: Counter) -> float:
    text = block["text"]
    lower = text.lower()
    words = len(text.split())
    if not words:
        return 40.0
    dur = max(1.0, block["end"] - block["start"])
    density = min(1.0, (words / dur) / 2.5)
    cues = sum(len(c.findall(lower)) for c in KEY_CUES)
    tangents = sum(len(c.findall(lower)) for c in TANGENT_CUES)
    richness = min(1.0, len(tf) / max(1.0, words * 0.45))
    score = 10 + 30 * density + min(45, cues * 5) + 15 * richness - min(45, tangents * 15)
    return round(max(0.0, min(100.0, score)), 1)


def _title(keywords: List[str]) -> str:
    return " · ".join(w.capitalize() for w in keywords) if keywords else "Lecture segment"


def build_lecture_cut(subtitles: List[Dict[str, Any]], shot_cuts: List[float], silences, video_duration: float,
                      target_duration_sec: float, energy: Optional[List[float]] = None) -> List[Dict[str, Any]]:
    blocks = segment_lecture(subtitles, shot_cuts, silences, video_duration)
    if not blocks:
        return []
    df: Counter = Counter()
    tfs = []
    for b in blocks:
        tf = Counter(_tokens(b["text"]))
        tfs.append(tf)
        df.update(set(tf))
    for b, tf in zip(blocks, tfs):
        b["keywords"] = _keywords(tf, df, len(blocks))
        b["score"] = _score_block(b, tf)

    target = max(60.0, min(float(target_duration_sec), video_duration * 0.9))
    regions = max(3, min(10, int(round(target / 75.0))))
    cap = max(45.0, min(240.0, target / regions * 1.35))   # one topic excerpt; a 5-minute cut covers 4 topics
    region_len = video_duration / regions
    chosen = set()
    for r in range(regions):
        in_region = [i for i, b in enumerate(blocks) if r * region_len <= (b["start"] + b["end"]) / 2 < (r + 1) * region_len]
        if in_region:
            chosen.add(max(in_region, key=lambda i: blocks[i]["score"]))

    def total() -> float:
        return sum(min(cap, blocks[i]["end"] - blocks[i]["start"]) for i in chosen)

    for i in sorted(range(len(blocks)), key=lambda i: -blocks[i]["score"]):
        if total() >= target:
            break
        chosen.add(i)
    while total() > target * 1.15 and len(chosen) > 1:
        chosen.remove(min(chosen, key=lambda i: blocks[i]["score"]))

    scenes = []
    for n, i in enumerate(sorted(chosen), 1):
        b = blocks[i]
        s = _snap_start(b["start"], shot_cuts, silences, 1.0)
        e = _snap_end(min(b["end"], b["start"] + cap), shot_cuts, silences, video_duration, 1.5)
        if e - s < 5.0:
            s, e = b["start"], b["end"]
        kw = b["keywords"]
        scenes.append({
            "act": f"Topic {n}",
            "start": s,
            "end": e,
            "title": _title(kw),
            "topic": ", ".join(kw) if kw else "the next topic",
            "keywords": kw,
            "dialogue": b["text"][:600],
            "importance": int(b["score"]),
            "reason": f"Key explanation covering {', '.join(kw)}" if kw else "Key lecture segment",
            "selected": True,
            "source": "lecture"
        })
    scenes = _resolve_overlaps(scenes, video_duration)
    return _fit_to_budget(scenes, target)


def gemini_lecture_cut(transcript_text: str, target_minutes: int, total_duration_sec: float, api_key: str) -> Dict[str, Any]:
    """Gemini picks the study segments, key points and flashcards."""
    from backend.video_engine.gemini_client import generate_json
    from backend.video_engine.subtitle_parser import parse_srt_time
    prompt = f"""You are a teaching assistant. Condense this recorded lecture ({int(total_duration_sec // 60)} minutes)
into a {target_minutes}-minute study cut. Choose the segments that explain key concepts, definitions, worked
examples and summaries. Skip housekeeping, audio checks, tangents and long pauses. Segments must be 45-240 seconds,
chronological, and total about {target_minutes * 60} seconds.

Transcript:
---
{transcript_text}
---

Return JSON: {{"overview": "2 sentences on what the lecture covers",
 "segments": [{{"start_time": "00:12:30", "end_time": "00:15:10", "topic": "Chain rule",
   "key_points": ["..."], "flashcards": [{{"q": "...", "a": "..."}}]}}]}}"""
    data = generate_json(api_key, prompt, temperature=0.2, timeout=180)
    scenes = []
    for n, seg in enumerate(data.get("segments", []), 1):
        start = parse_srt_time(str(seg.get("start_time", "0:00:00")))
        end = min(parse_srt_time(str(seg.get("end_time", "0:00:00"))), total_duration_sec)
        if end - start < 10:
            continue
        topic = str(seg.get("topic", f"Topic {n}"))
        points = [str(p) for p in seg.get("key_points", [])][:6]
        cards = [{"q": str(c.get("q", "")), "a": str(c.get("a", ""))} for c in seg.get("flashcards", []) if c.get("q")]
        scenes.append({"act": f"Topic {n}", "start": start, "end": end, "title": topic, "topic": topic,
                       "reason": "; ".join(points[:2]) or f"Explains {topic}", "key_points": points,
                       "flashcards": cards[:4], "importance": 80, "selected": True, "source": "gemini_lecture"})
    return {"overview": data.get("overview", ""), "scenes": scenes}


def key_points_for(scene: Dict[str, Any], subtitles: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    lines = [s["text"] for s in subtitles if s["end"] > scene["start"] and s["start"] < scene["end"]]
    picked = [l for l in lines if any(c.search(l.lower()) for c in KEY_CUES)]
    if len(picked) < 2:
        picked += sorted(lines, key=lambda l: -len(_tokens(l)))[:limit]
    seen, out = set(), []
    for l in picked:
        if l not in seen and len(l.split()) >= 4:
            seen.add(l)
            out.append(l)
        if len(out) >= limit:
            break
    return out


def flashcards_for(text: str, limit: int = 3) -> List[Dict[str, str]]:
    """Builds Q/A cards from definition phrases ('X is defined as Y', 'X is called Y', 'X means Y')."""
    cards, seen = [], set()
    for m in DEFINITION_CUE_RE.finditer(text):
        before = text[:m.start()].split()[-5:]
        cut = max((i for i, w in enumerate(before) if w.lower() in ARTICLES), default=-1)
        words = before[cut + 1:] if cut >= 0 else before[-2:]
        words = [w for w in words if w.lower() not in STOPWORDS] or words
        term = " ".join(words[-3:]).strip(" ,;:")
        if not term or len(term) < 3 or term.lower() in seen or term.lower() in STOPWORDS:
            continue
        nxt = DEFINITION_CUE_RE.search(text, m.end())
        stop = min(m.end() + 200, nxt.start() if nxt else len(text))
        after = re.split(r"[.?!]", text[m.end():stop])[0].split()[:25]
        if nxt and len(after) > 4:
            after = after[:-3] if len(after) > 7 else after  # drop the next definition's subject words
        if len(after) < 4:
            continue
        verb = (m.group(1) or m.group(3) or "is").lower()
        cue = (m.group(2) or "").lower()
        seen.add(term.lower())
        q = f"What does {term} mean?" if verb == "means" else f"What {verb} {term}?"
        while after and after[-1].lower() in STOPWORDS:
            after = after[:-1]
        cards.append({"q": q, "a": f"{term} {verb} {cue + ' ' if cue else ''}{' '.join(after)}".replace("  ", " ")})
        if len(cards) >= limit:
            break
    return cards


def generate_study_notes(scenes: List[Dict[str, Any]], subtitles: List[Dict[str, Any]], title: str,
                         durations: Optional[List[float]] = None) -> str:
    """Markdown study notes: one section per kept topic with timestamps, key points and flashcards."""
    active = sorted([s for s in scenes if s.get("selected", True)], key=lambda s: s["start"])
    lines = [f"# Study notes: {title}", "",
             "Generated by CineCut from the lecture transcript. Check facts against the original lecture.", ""]
    offset, cards_all = 0.0, []
    for n, s in enumerate(active, 1):
        d = durations[n - 1] if durations and n - 1 < len(durations) else s.get("duration", s["end"] - s["start"])
        lines.append(f"## {n}. {s.get('title', 'Topic')}")
        lines.append(f"*Lecture {format_seconds(s['start'])} to {format_seconds(s['end'])} · study cut at {format_seconds(offset)}*")
        lines.append("")
        for p in s.get("key_points") or key_points_for(s, subtitles):
            lines.append(f"- {p}")
        scene_text = " ".join(x["text"] for x in subtitles if x["end"] > s["start"] and x["start"] < s["end"])
        cards_all += s.get("flashcards") or flashcards_for(scene_text)
        lines.append("")
        offset += d
    if cards_all:
        lines += ["## Flashcards", ""]
        for c in cards_all:
            lines += [f"**Q:** {c['q']}  ", f"**A:** {c['a']}", ""]
    return "\n".join(lines)


def enrich_topics_with_ai(scenes: List[Dict[str, Any]], subtitles: List[Dict[str, Any]],
                          api_key: Optional[str] = None, cache: Optional[Dict[Any, Any]] = None) -> List[Dict[str, Any]]:
    """Gives each kept topic an AI-written title, key points and flashcards drawn only from its transcript."""
    from backend.video_engine import llm_client
    cache = cache if cache is not None else {}
    for s in scenes:
        key = f"{round(s['start'])}-{round(s['end'])}"
        if key in cache:
            s.update(cache[key])
            continue
        text = " ".join(x["text"] for x in subtitles if x["end"] > s["start"] and x["start"] < s["end"])
        if len(text.split()) < 40:
            continue
        prompt = ("This is the transcript of one part of a lecture. Using ONLY this transcript, return JSON with:\n"
                  '"title": a 3-7 word topic title,\n'
                  '"key_points": 2-4 short points a student should remember,\n'
                  '"flashcards": 1-3 objects {"q": question, "a": answer} answerable from the transcript.\n'
                  "Do not add facts that are not in the transcript.\n\nTranscript:\n---\n" + text[:6000] + "\n---")
        data = llm_client.generate_json(prompt, api_key=api_key, temperature=0.2, timeout=150)
        upd: Dict[str, Any] = {"notes_source": llm_client.describe(api_key)}
        title = str(data.get("title", "")).strip().strip('"')
        if 2 <= len(title.split()) <= 10:
            upd["title"] = title
            upd["topic"] = title
        points = [str(p).strip() for p in data.get("key_points", []) if str(p).strip()][:4]
        if points:
            upd["key_points"] = points
            upd["reason"] = points[0]
        cards = [{"q": str(c.get("q", "")).strip(), "a": str(c.get("a", "")).strip()}
                 for c in data.get("flashcards", []) if isinstance(c, dict) and c.get("q") and c.get("a")][:3]
        if cards:
            upd["flashcards"] = cards
        cache[key] = upd
        s.update(upd)
    return scenes
