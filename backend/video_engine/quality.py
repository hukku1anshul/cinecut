"""
Quality score for every cut (0-100), plus an AI check and an automatic fix.

score_cut():   measured without AI, instantly, after every analysis or change:
               story coverage (core beats kept), story spread (no huge skipped stretch), clean edges (no clip cuts
               into a spoken line), length match, and narration quality (lines written from the skipped part).
ai_check():    an AI reads the cut like a first-time viewer (the full story's beats vs. the kept scenes and the
               narration) and lists where the viewer would get lost, with the story beat that would fix it.
fix_cut():     adds the missing beat (or the most important dialogue in that skipped stretch) where the viewer
               gets lost, then trims the cut back to the requested length on line breaks.
"""
import hashlib
from typing import Any, Dict, List, Optional

from backend.video_engine import llm_client
from backend.video_engine.flavor_detector import ANY_TAG
from backend.video_engine.probe import format_seconds
from backend.video_engine.summarizer import fit_and_snap, speech_index

GROUNDED = ("ai", "ai_visual", "database", "handwritten", "ai_knowledge", "intro")
WEIGHTS = {"story_coverage": 0.35, "story_spread": 0.15, "clean_edges": 0.15, "length_match": 0.15, "narration": 0.20}
LABELS = {"story_coverage": "Story beats kept", "story_spread": "No huge skipped stretch", "clean_edges": "Clips end between lines",
          "length_match": "Length as asked", "narration": "Narration from the skipped part", "ai_coherence": "AI viewer check"}


def _cues(subs):
    return [(float(s["start"]), float(s["end"])) for s in subs or [] if ANY_TAG.sub(" ", s.get("text", "")).strip()]


def _selected(job):
    return sorted([s for s in job.get("scenes", []) if s.get("selected", True)], key=lambda s: s["start"])


def cut_signature(job) -> str:
    return hashlib.sha1("|".join(f"{s['start']:.1f}-{s['end']:.1f}" for s in _selected(job)).encode()).hexdigest()[:12]


def _beats(job) -> List[Dict[str, Any]]:
    if job.get("content_mode") == "lecture":
        return []
    ms = list(((job.get("essence") or {}).get("milestones") or []))
    if (job.get("essence") or {}).get("is_generic"):
        return []
    if job.get("spoiler_mode") == "teaser_spoiler_free":
        ms = [m for m in ms if float(m.get("target_percent") or 0) <= 0.65]
    return ms


def _beat_span(m):
    a = float(m.get("start_sec", m.get("target_time_sec", 0)) or 0)
    b = float(m.get("end_sec", a + 60) or a + 60)
    return a, b


def _covered(m, scenes) -> bool:
    """A beat counts as kept when a real share of it is in the cut (20 s or half the beat), not a few seconds."""
    a, b = _beat_span(m)
    overlap = sum(max(0.0, min(b, s["end"]) - max(a, s["start"])) for s in scenes)
    return overlap >= min(20.0, 0.5 * max(1.0, b - a))


def _free_piece(a: float, b: float, scenes, min_len: float = 12.0, max_len: float = 45.0):
    """Longest part of [a, b] that no kept clip uses (at least min_len), cut to max_len."""
    pieces = [(a, b)]
    for s in sorted(scenes, key=lambda x: x["start"]):
        nxt = []
        for x, y in pieces:
            if s["end"] <= x or s["start"] >= y:
                nxt.append((x, y))
                continue
            if s["start"] - x >= min_len:
                nxt.append((x, s["start"] - 0.5))
            if y - s["end"] >= min_len:
                nxt.append((s["end"] + 0.5, y))
        pieces = nxt
    pieces = [p for p in pieces if p[1] - p[0] >= min_len]
    if not pieces:
        return None
    x, y = max(pieces, key=lambda p: p[1] - p[0])
    return x, min(y, x + max_len)


def score_cut(job: Dict[str, Any]) -> Dict[str, Any]:
    scenes = _selected(job)
    if not scenes or not job.get("metadata"):
        return {"score": 0, "parts": {}, "issues": ["No scenes are selected."]}
    dur = float(job["metadata"]["duration_sec"])
    target = float(job.get("target_minutes") or 0) * 60.0
    teaser = job.get("spoiler_mode") == "teaser_spoiler_free"
    parts: Dict[str, int] = {}
    issues: List[str] = []

    beats = _beats(job)
    if beats:
        core = [m for m in beats if int(m.get("tier", 1) or 1) == 1] or beats
        core_cov = sum(_covered(m, scenes) for m in core) / len(core)
        all_cov = sum(_covered(m, scenes) for m in beats) / len(beats)
        parts["story_coverage"] = round(100 * (0.7 * core_cov + 0.3 * all_cov))
        missing = [m.get("title", "?") for m in core if not _covered(m, scenes)]
        if missing:
            issues.append("Core story beats left out: " + "; ".join(missing[:4]))

    limit = dur * (0.65 if teaser else 1.0)
    gaps = [(0.0, scenes[0]["start"])] + [(a["end"], b["start"]) for a, b in zip(scenes, scenes[1:])]
    if not teaser:
        gaps.append((scenes[-1]["end"], dur))
    g0, g1 = max(gaps, key=lambda g: g[1] - g[0])
    frac = (g1 - g0) / max(1.0, limit)
    parts["story_spread"] = int(max(0, min(100, round(100 * (1 - max(0.0, frac - 0.12) / 0.33)))))
    if frac > 0.25:
        issues.append(f"{round((g1 - g0) / 60)} minutes are skipped in one go ({format_seconds(g0)} to {format_seconds(g1)})")

    cues = _cues(job.get("subtitles"))
    if cues:
        inside, _ = speech_index(cues)
        mid = sum(1 for s in scenes if inside(s["start"])) + sum(1 for s in scenes if inside(s["end"]))
        parts["clean_edges"] = round(100 * (1 - mid / (2 * len(scenes))))
        if mid:
            issues.append(f"{mid} clip edge{'s' if mid != 1 else ''} cut into a spoken line")

    if target > 0:
        total = sum(s["end"] - s["start"] for s in scenes)
        dev = abs(total - target) / target
        parts["length_match"] = int(max(0, round(100 - 400 * max(0.0, dev - 0.02))))
        if dev > 0.05:
            issues.append(f"The cut is {format_seconds(total)} instead of {format_seconds(target)}")

    voiced = [s for s in scenes if s.get("bridge_narration")]
    if voiced:
        good = sum(1 for s in voiced if s.get("narration_source") in GROUNDED)
        parts["narration"] = round(100 * good / len(voiced))
        skips = len(voiced) - good
        if skips:
            issues.append(f"{skips} narration line{'s are' if skips != 1 else ' is'} only a time skip")

    used = {k: w for k, w in WEIGHTS.items() if k in parts}
    score = sum(parts[k] * w for k, w in used.items()) / max(1e-6, sum(used.values()))
    ai = (job.get("quality") or {}).get("ai_check")
    if ai and ai.get("signature") == cut_signature(job) and ai.get("coherence") is not None:
        parts["ai_coherence"] = int(round(float(ai["coherence"]) * 10))
        score = 0.8 * score + 0.2 * parts["ai_coherence"]
        issues += [f"Viewer may get lost before scene {p.get('before_scene')}: {p.get('why')}" for p in ai.get("lost_points", [])[:3]]
    return {"score": int(round(score)), "parts": parts, "labels": {k: LABELS[k] for k in parts}, "issues": issues,
            "ai_check": ai if ai and ai.get("signature") == cut_signature(job) else None}


JUDGE_PROMPT = """You are checking a condensed version of the film "{title}" before it is published.
The full story, in order:
{beats}

The condensed version keeps only these scenes, in this order (narration is spoken before some of them):
{cut}

Think like a first-time viewer who sees only the condensed version and hears the narration.
Return JSON:
{{"coherence": 0-10 (10 means the story is completely clear),
  "lost_points": [{{"before_scene": <number of the kept scene where the viewer gets confused>, "why": "one sentence",
                   "missing_beat": "exact title of the story beat above that would fix it, or empty"}}],
  "narration_problems": [{{"scene": <number>, "problem": "one sentence"}}]}}
List at most 5 lost points, most serious first. Report only real problems; an empty list is fine."""


def ai_check(job: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
    beats = _beats(job)
    scenes = _selected(job)
    if not beats or not scenes:
        return {"error": "The AI check needs a film with story beats."}
    tiers = {1: "core", 2: "supporting", 3: "extra"}
    beat_lines = "\n".join(f"- [{format_seconds(_beat_span(m)[0])}] {m.get('title', '')}: {m.get('description', '')} "
                           f"({tiers.get(int(m.get('tier', 1) or 1), 'core')})" for m in beats)
    cut_lines = []
    for n, s in enumerate(scenes, 1):
        said = " ".join(str(s.get("dialogue") or "").split())[:180]
        narr = s.get("narration_english") or s.get("bridge_narration")
        cut_lines.append(f"{n}. [{s['start_formatted']}-{s['end_formatted']}] {s.get('title', '')}"
                         + (f" | narration before it: {narr}" if narr else "") + (f" | dialogue: {said}" if said else ""))
    prompt = JUDGE_PROMPT.format(title=(job.get("film_title") or "the film"), beats=beat_lines[:9000],
                                 cut="\n".join(cut_lines)[:12000])
    data = llm_client.generate_json(prompt, api_key=api_key, temperature=0.1, timeout=180, num_ctx=16384)
    if not isinstance(data, dict):
        return {"error": "The AI check returned no answer."}
    try:
        coherence = max(0.0, min(10.0, float(data.get("coherence", 0))))
    except (TypeError, ValueError):
        coherence = 0.0
    lost = []
    for p in data.get("lost_points") or []:
        if not isinstance(p, dict):
            continue
        try:
            n = int(p.get("before_scene"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(scenes):
            lost.append({"before_scene": n, "why": str(p.get("why", ""))[:240], "missing_beat": str(p.get("missing_beat") or "")[:120]})
    probs = [{"scene": q.get("scene"), "problem": str(q.get("problem", ""))[:240]} for q in (data.get("narration_problems") or [])
             if isinstance(q, dict)]
    return {"coherence": coherence, "lost_points": lost[:5], "narration_problems": probs[:5], "signature": cut_signature(job),
            "checked_by": llm_client.last_used.get("provider", "AI")}


def _new_scene(start: float, end: float, title: str, reason: str, dialogue: str = "") -> Dict[str, Any]:
    return {"act": "Added by the quality check", "start": round(start, 2), "end": round(end, 2), "duration": round(end - start, 2),
            "start_formatted": format_seconds(start), "end_formatted": format_seconds(end), "title": title, "dialogue": dialogue[:600],
            "importance": 90, "reason": reason, "selected": True, "source": "quality_fix", "tier": 1, "tags": [],
            "filter_match": [], "is_continuation": False, "exact_boundaries": False}


def fix_cut(job: Dict[str, Any], check: Dict[str, Any]) -> Dict[str, Any]:
    """Adds a scene where the viewer gets lost, then fits the cut back to the requested length."""
    from backend.video_engine.subtitle_parser import group_dialogue_into_scenes, score_dialogue_significance
    scenes = _selected(job)
    beats = _beats(job)
    subs = job.get("subtitles") or []
    dur = float(job["metadata"]["duration_sec"])
    added = []
    for p in check.get("lost_points", [])[:4]:
        n = p["before_scene"]
        gap_a = scenes[n - 2]["end"] if n >= 2 else 0.0
        gap_b = scenes[n - 1]["start"]
        want = p.get("missing_beat", "").lower().strip()
        beat = next((m for m in beats if want and (want in m.get("title", "").lower() or m.get("title", "").lower() in want)), None)
        new = None
        if beat and not _covered(beat, scenes + added):
            a, b = _beat_span(beat)
            piece = _free_piece(a, max(b, a + 60.0), scenes + added)
            if piece:
                new = _new_scene(piece[0], piece[1], beat.get("title", "Story beat"),
                                 f"Added so the story stays clear: {p['why']}", beat.get("description", ""))
        if new is None and gap_b - gap_a > 30:
            # no beat named (or it is already in): the most important story beat inside that skipped stretch
            unused = [m for m in beats if gap_a <= _beat_span(m)[0] < gap_b and not _covered(m, scenes + added)]
            unused.sort(key=lambda m: (int(m.get("tier", 2) or 2), -float(m.get("importance", 50))))
            for m in unused:
                a, b = _beat_span(m)
                piece = _free_piece(a, max(b, a + 60.0), scenes + added)
                if piece:
                    new = _new_scene(piece[0], piece[1], m.get("title", "Story beat"),
                                     f"Added so the story stays clear: {p['why']}", m.get("description", ""))
                    break
        if new is None and gap_b - gap_a > 60:
            groups = [g for g in group_dialogue_into_scenes(subs) if g["start"] >= gap_a + 5 and g["start"] <= gap_b - 20]
            if groups:
                g = max(groups, key=lambda g: score_dialogue_significance(g["dialogue"], g["duration"]))
                new = _new_scene(g["start"], min(g["end"], gap_b - 5, g["start"] + 45.0), "Bridge scene",
                                 f"Added so the story stays clear: {p['why']}", g["dialogue"])
        if new and all(new["end"] <= s["start"] or new["start"] >= s["end"] for s in scenes + added):
            added.append(new)
    if not added:
        return {"added": 0}
    target =float(job.get("target_minutes") or 0) * 60.0
    keep = scenes + added
    fitted = fit_and_snap(sorted(keep, key=lambda s: s["start"]), _cues(subs), target, dur) if target else keep
    unselected = [s for s in job["scenes"] if not s.get("selected", True)]
    job["scenes"] = sorted(fitted + unselected, key=lambda s: s["start"])
    job.setdefault("quality", {})["ai_check"] = None
    return {"added": len(added), "added_scenes": [{"at": s["start_formatted"], "title": s["title"]} for s in added]}
