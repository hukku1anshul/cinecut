from bisect import bisect_right
from typing import List, Dict, Any, Optional, Tuple
from backend.config import (
    PRESETS,
    DEFAULT_MIN_SCENE_SEC,
    DEFAULT_MAX_SCENE_SEC,
    PADDING_HEAD_SEC,
    PADDING_TAIL_SEC
)
from backend.video_engine.probe import format_seconds
from backend.video_engine.audio_analyzer import snap_timestamp_to_silence
from backend.video_engine.scene_detector import snap_timestamp_to_shot

MIN_CLIP_SEC = 8.0
BEAT_CAP_SEC = 90.0   # beyond this, extra time goes to bridge scenes instead of longer beat clips

# Words that mark a story beat as matching a filter. Used for beats without AI tags (the built-in database).
FILTER_KEYWORDS = {
    "action": ["fight", "battle", "chase", "climax", "showdown", "missile", "explosion", "smuggler", "thrash",
               "action", "attack", "war", "escape", "heist", "kick", "train", "gun", "shoot", "duel"],
    "song": ["song", "dance", "music", "sings", "singing", "reet", "hawa hawai", "celebrat", "festival"],
    "romance": ["romance", "love", "rain", "kiss", "propos", "marriage", "wedding", "romantic", "spark"],
    "comedy": ["tenant", "humor", "comedy", "calendar", "prank", "chaos", "bicker", "funny", "laugh", "joke", "comic"],
    "emotional": ["tragedy", "sacrifice", "martyr", "grief", "tears", "death", "dies", "loss", "backstory",
                  "darkest", "crisis", "farewell", "confess", "apolog", "guilt", "heartbreak"],
    "villain": ["villain", "mogambo", "gabbar", "fortress", "acid", "citadel", "threat", "antagonist", "scheme",
                "kitne aadmi", "gang", "henchm", "kidnap", "mafia", "corrupt", "smuggl"],
    "hero": ["hero", "mr. india", "invisib", "vigilante", "protagonist", "triumph", "rescue", "recruit",
             "saves", "stands up", "fights back"],
}
FILTER_LABELS = {"song": "Songs", "action": "Action", "comedy": "Comedy", "emotional": "Emotional",
                 "romance": "Romance", "hero": "Hero", "villain": "Villain"}
FILTER_FOR_PRESET = {"action_energy": ["action"], "musical_romance": ["song", "romance"], "comedy_fun": ["comedy"],
                     "emotional_drama": ["emotional"], "hero_spotlight": ["hero"], "villain_lore": ["villain"]}


def _score(c: Dict[str, Any], weights: Dict[str, float]) -> float:
    return (c.get("dialogue_score", 50.0) * weights.get("dialogue", 0.4) +
            c.get("audio_score", 50.0) * weights.get("audio_dynamic", 0.3) +
            c.get("motion_score", 50.0) * weights.get("motion", 0.2))


def _format_scene(s: Dict[str, Any]) -> Dict[str, Any]:
    s["start"] = round(float(s["start"]), 2)
    s["end"] = round(float(s["end"]), 2)
    s["duration"] = round(s["end"] - s["start"], 2)
    s["start_formatted"] = format_seconds(s["start"])
    s["end_formatted"] = format_seconds(s["end"])
    return s


def _snap_start(t: float, shot_cuts, silences, max_shift: float = 1.5) -> float:
    t2 = snap_timestamp_to_shot(t, shot_cuts, max_shift=max_shift)
    t2 = snap_timestamp_to_silence(t2, silences, is_end_boundary=False, max_shift=max_shift)
    return max(0.0, t2 - PADDING_HEAD_SEC)


def _snap_end(t: float, shot_cuts, silences, video_duration: float, max_shift: float = 2.0) -> float:
    t2 = snap_timestamp_to_silence(t, silences, is_end_boundary=True, max_shift=max_shift)
    t2 = snap_timestamp_to_shot(t2, shot_cuts, max_shift=max_shift)
    return min(video_duration, t2 + PADDING_TAIL_SEC) if video_duration > 0 else t2 + PADDING_TAIL_SEC


def _resolve_overlaps(scenes: List[Dict[str, Any]], video_duration: float, min_len: float = 3.0) -> List[Dict[str, Any]]:
    """Clamps to the video, drops invalid clips, merges overlaps, and adds formatted times."""
    valid = []
    for s in scenes:
        s["start"] = max(0.0, float(s["start"]))
        if video_duration > 0:
            s["end"] = min(video_duration, float(s["end"]))
        if s["end"] - s["start"] >= min_len:
            valid.append(s)
    valid.sort(key=lambda x: x["start"])
    out: List[Dict[str, Any]] = []
    for s in valid:
        if out and s["start"] < out[-1]["end"] - 0.05:
            if s["end"] > out[-1]["end"]:
                out[-1]["end"] = s["end"]
                out[-1]["exact_boundaries"] = False
        else:
            out.append(s)
    return [_format_scene(s) for s in out]


def _fit_to_budget(scenes: List[Dict[str, Any]], target: float) -> List[Dict[str, Any]]:
    """Trims clip tails proportionally if the cut overshoots the requested length by more than 8%."""
    total = sum(s["end"] - s["start"] for s in scenes)
    if total <= target * 1.03 or total <= 0:
        return scenes
    flexible = [s for s in scenes if not s.get("exact_boundaries")]
    fixed_total = sum(s["end"] - s["start"] for s in scenes if s.get("exact_boundaries"))
    flex_total = total - fixed_total
    if flex_total <= 0:
        return scenes
    factor = max(0.1, (target - fixed_total) / flex_total)
    for s in flexible:
        new_len = max(MIN_CLIP_SEC, (s["end"] - s["start"]) * factor)
        s["end"] = s["start"] + new_len
    return [_format_scene(s) for s in scenes]


def refine_scene_boundaries(
    scenes: List[Dict[str, Any]],
    shot_cuts: List[float],
    silences: List[Tuple[float, float]],
    video_duration: float
) -> List[Dict[str, Any]]:
    """Polishes externally chosen cuts (e.g. from Gemini) using shot cuts and dialogue pauses."""
    refined = []
    for s in scenes:
        sc = dict(s)
        if not sc.get("exact_boundaries", False):
            st = _snap_start(sc["start"], shot_cuts, silences, max_shift=1.2)
            en = _snap_end(sc["end"], shot_cuts, silences, video_duration, max_shift=2.0)
            if en - st >= 5.0:
                sc["start"], sc["end"] = st, en
        refined.append(sc)
    return _resolve_overlaps(refined, video_duration, min_len=5.0)


def _act_for_position(pos: float, teaser: bool) -> str:
    if pos < 0.25:
        return "Act 1"
    if pos < 0.5:
        return "Act 2A"
    if pos < 0.75 or teaser:
        return "Act 2B"
    return "Act 3"


def _flavor_clips(segments, target, share, video_dur, effective_max, teaser, shot_cuts, silences):
    """Turns detected song/action/comedy/emotional segments into clips, using up to `share` of the budget."""
    from backend.video_engine.flavor_detector import MAX_CLIP
    reserve = target * share
    clips: List[Dict[str, Any]] = []
    used = 0.0
    for seg in segments:
        if reserve - used < MIN_CLIP_SEC * 1.5:
            break
        if seg["end"] > effective_max:
            continue
        seg_len = seg["end"] - seg["start"]
        length = min(seg_len, MAX_CLIP.get(seg["flavor"], 75.0), reserve - used)
        if length < MIN_CLIP_SEC * 1.5:
            continue
        start = seg["start"] if seg["flavor"] == "song" else seg["start"] + (seg_len - length) / 2.0
        end = start + length
        if any(start < c["end"] and end > c["start"] for c in clips):
            continue
        s0 = _snap_start(start, shot_cuts, silences)
        e0 = _snap_end(end, shot_cuts, silences, video_dur)
        if e0 - s0 >= MIN_CLIP_SEC:
            start, end = s0, e0
        act = _act_for_position(start / max(1.0, video_dur), teaser)
        clips.append({"act": f"{act}: {seg['label']}", "start": start, "end": end,
                      "title": f"{seg['label']} at {format_seconds(start)}", "dialogue": "",
                      "importance": int(seg["score"]), "reason": f"Picked for your flavor profile ({seg['evidence']})",
                      "selected": True, "source": "flavor", "flavor": seg["flavor"], "is_continuation": False,
                      "tags": [seg["flavor"]], "filter_match": [seg["flavor"]],
                      "exact_boundaries": False})
        used += end - start
    return clips, used


def _subtract_intervals(scenes: List[Dict[str, Any]], blockers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Removes the parts of story clips that overlap genre clips, keeping pieces of at least MIN_CLIP_SEC."""
    out = []
    for sc in scenes:
        pieces = [(sc["start"], sc["end"])]
        for b in blockers:
            nxt = []
            for a, e in pieces:
                if e <= b["start"] or a >= b["end"]:
                    nxt.append((a, e))
                    continue
                if b["start"] - a >= MIN_CLIP_SEC:
                    nxt.append((a, b["start"]))
                if e - b["end"] >= MIN_CLIP_SEC:
                    nxt.append((b["end"], e))
            pieces = nxt
        for n, (a, e) in enumerate(pieces):
            piece = dict(sc)
            piece["start"], piece["end"] = a, e
            if n > 0:
                piece["is_continuation"] = True
                piece["voiceover_text"] = piece["narration_hi"] = piece["narration_en"] = None
            out.append(piece)
    return out


def _top_up(scenes: List[Dict[str, Any]], target: float, limit: float) -> List[Dict[str, Any]]:
    """If the cut is short of the target, lets clips run on into the footage that follows them."""
    ordered = sorted(scenes, key=lambda x: x["start"])
    for story_only in (True, False):
        for _ in range(3):
            deficit = target - sum(x["end"] - x["start"] for x in ordered)
            if deficit < target * 0.03:
                return [_format_scene(x) for x in ordered]
            room = []
            for j, x in enumerate(ordered):
                nxt = ordered[j + 1]["start"] if j + 1 < len(ordered) else limit
                free = max(0.0, nxt - x["end"] - 1.0)
                room.append(0.0 if (story_only and x.get("source") == "flavor") else free)
            total_room = sum(room)
            if total_room <= 0:
                break
            for x, r in zip(ordered, room):
                x["end"] += min(r, deficit * r / total_room)
    return [_format_scene(x) for x in ordered]


def beat_tags(m: Dict[str, Any]) -> List[str]:
    """Filter tags of a story beat: the AI's tags when it gave them, otherwise keywords in its title/description."""
    if isinstance(m.get("tags"), list):
        return [t for t in m["tags"] if t in FILTER_KEYWORDS]
    text = (m.get("title", "") + " " + m.get("description", "")).lower()
    return [f for f, words in FILTER_KEYWORDS.items() if any(w in text for w in words)]


def _focus_hit(m: Dict[str, Any], focus_names: Optional[List[str]]) -> bool:
    if not focus_names:
        return False
    text = (m.get("title", "") + " " + m.get("description", "") + " " + " ".join(m.get("characters") or [])).lower()
    return any(n and n.lower() in text for n in focus_names)


def _beat_priority(m: Dict[str, Any], filters: Optional[List[str]], focus_names: Optional[List[str]]) -> float:
    boost = 1.0
    if filters and set(beat_tags(m)) & set(filters):
        boost *= 1.6
    if _focus_hit(m, focus_names):
        boost *= 1.5
    return boost


def _match_note(m: Dict[str, Any], filters: Optional[List[str]], focus_names: Optional[List[str]]) -> str:
    parts = [FILTER_LABELS.get(t, t) for t in sorted(set(beat_tags(m)) & set(filters or []))]
    if _focus_hit(m, focus_names):
        parts.append("your chosen character")
    return f" · Picked for your filters: {', '.join(parts)}" if parts else ""


def _beat_time(m: Dict[str, Any]) -> float:
    return float(m.get("target_time_sec", m.get("start_sec", 0.0)) or 0.0)


def _beat_pos(m: Dict[str, Any], video_dur: float) -> float:
    if m.get("target_percent") is not None:
        return float(m["target_percent"])
    return _beat_time(m) / max(1.0, video_dur)


def select_beats(milestones: List[Dict[str, Any]], target: float, filters: Optional[List[str]] = None,
                 focus_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Chooses the story beats that fit the requested length. Core beats (tier 1) always come first; supporting
    (tier 2) and extra (tier 3) beats are added as the length grows, so a 30-minute cut shows more of the
    story instead of stretching the same few scenes. Beats that match the viewer's filters or chosen
    character move up one tier. The opening and the resolution are never dropped.
    """
    if not milestones:
        return []

    def tier(m):
        t = max(1, min(3, int(m.get("tier", 1) or 1)))
        return t - 1 if t > 1 and _beat_priority(m, filters, focus_names) > 1.0 else t

    desired = 35.0 + 1.2 * (target / 60.0)          # ~41 s per beat at 5 min, ~59 s at 20 min, ~71 s at 30 min
    max_core = max(3, int(target // 25))
    max_total = max(3, int(target // desired))
    core = [m for m in milestones if tier(m) == 1]
    rest = sorted([m for m in milestones if tier(m) > 1],
                  key=lambda m: (tier(m), -float(m.get("importance", 70)) * _beat_priority(m, filters, focus_names)))
    if len(core) > max_core:
        by_time = sorted(core, key=_beat_time)
        others = sorted(by_time[1:-1], key=lambda m: -float(m.get("importance", 80)) * _beat_priority(m, filters, focus_names))
        core = [by_time[0], by_time[-1]] + others[:max_core - 2]
    chosen = list(core)
    for m in rest:
        if len(chosen) >= max_total:
            break
        chosen.append(m)
    return sorted(chosen, key=_beat_time)


def speech_index(cues: Optional[List[Tuple[float, float]]]):
    """
    Returns (inside, clean): inside(t) tells whether t falls in the middle of any spoken line (more than 0.3 s
    from its edges, overlapping captions included); clean is the sorted list of line edges that no other line
    covers - the places where a cut does not interrupt anyone.
    """
    # a caption that stays up for more than 12 s (song lyrics, badly timed auto-captions) does not mark one
    # spoken line, so it is ignored here; pause detection still keeps those cuts off speech
    cues = sorted(c for c in (cues or []) if c[1] - c[0] <= 12.0)
    starts = [c[0] for c in cues]
    maxend, m = [], -1.0
    for c in cues:
        m = max(m, c[1])
        maxend.append(m)

    def inside(t: float, tol: float = 0.3) -> bool:
        k = bisect_right(starts, t - tol) - 1
        return k >= 0 and maxend[k] > t + tol

    bounds = sorted({round(x, 2) for c in cues for x in c})
    clean = [b for b in bounds if not inside(b, 0.05)]
    return inside, clean


def _sentence_snap(scenes: List[Dict[str, Any]], cues: Optional[List[Tuple[float, float]]],
                   video_dur: float) -> List[Dict[str, Any]]:
    """Moves clip edges that fall in the middle of a spoken line to the nearest line break."""
    if not cues or not scenes:
        return scenes
    inside, clean = speech_index(cues)
    if not clean:
        return scenes
    ordered = sorted(scenes, key=lambda x: x["start"])
    for n, s in enumerate(ordered):
        if s.get("exact_boundaries"):
            continue
        prev_end = ordered[n - 1]["end"] if n > 0 else 0.0
        next_start = ordered[n + 1]["start"] if n + 1 < len(ordered) else video_dur
        if inside(s["start"]):
            i = bisect_right(clean, s["start"])
            before = clean[i - 1] if i > 0 else None
            after = clean[i] if i < len(clean) else None
            if before is not None and s["start"] - before <= 4.0 and before >= prev_end + 0.3:
                s["start"] = max(0.0, before - 0.05)
            elif after is not None and after - s["start"] <= 8.0 and s["end"] - after >= MIN_CLIP_SEC:
                s["start"] = after
        if inside(s["end"]):
            i = bisect_right(clean, s["end"])
            before = clean[i - 1] if i > 0 else None
            after = clean[i] if i < len(clean) else None
            if after is not None and after - s["end"] <= 6.0 and after <= next_start - 0.3:
                s["end"] = min(video_dur, after + 0.05)
            elif before is not None and before - s["start"] >= MIN_CLIP_SEC and s["end"] - before <= 12.0:
                s["end"] = before
    return [_format_scene(s) for s in ordered]


def _trim_to_sentences(scenes: List[Dict[str, Any]], cues: Optional[List[Tuple[float, float]]],
                       target: float) -> List[Dict[str, Any]]:
    """If snapping to whole lines pushed the cut over the requested length, ends the longest clips one or more
    whole lines earlier until the cut fits again (never in the middle of a line)."""
    total = sum(x["end"] - x["start"] for x in scenes)
    if total <= target * 1.02:
        return scenes
    inside, clean = speech_index(cues)
    for sc in sorted([x for x in scenes if not x.get("exact_boundaries")], key=lambda x: -(x["end"] - x["start"])):
        excess = total - target * 1.01
        if excess <= 0:
            break
        length = sc["end"] - sc["start"]
        cut = min(excess, length - max(MIN_CLIP_SEC, length * 0.6))
        if cut <= 0.5:
            continue
        want = sc["end"] - cut
        if not cues or not inside(want):
            new_end = want
        else:
            options = [b for b in clean if want - 6.0 <= b <= sc["end"] - 0.5 and b >= sc["start"] + MIN_CLIP_SEC]
            if not options:
                continue
            new_end = min(options, key=lambda b: abs(b - want)) + 0.05
        if new_end < sc["end"] and new_end - sc["start"] >= MIN_CLIP_SEC:
            total -= sc["end"] - new_end
            sc["end"] = new_end
    return [_format_scene(x) for x in scenes]


def _bridge_scenes(scenes, candidates, extra, weights, shot_cuts, silences, video_dur, effective_max):
    """
    For long cuts: adds the most important dialogue scene from the longest stretches that are still cut out,
    so the story stays connected between beats instead of each beat clip being stretched further.
    """
    pool = [c for c in candidates if c.get("dialogue") and not c.get("synthetic")]
    added: List[Dict[str, Any]] = []
    remaining = extra
    for _ in range(60):
        if remaining < MIN_CLIP_SEC * 2 or not pool:
            break
        spans = sorted([(x["start"], x["end"]) for x in scenes] + [(x["start"], x["end"]) for x in added])
        gaps, prev = [], 0.0
        for a, b in spans:
            if a - prev > 60.0:
                gaps.append((prev, a))
            prev = max(prev, b)
        if effective_max - prev > 60.0:
            gaps.append((prev, effective_max))
        gaps.sort(key=lambda g: -(g[1] - g[0]))
        placed = False
        for ga, gb in gaps[:3]:
            inside = [c for c in pool if c["start"] >= ga + 5.0 and c["end"] <= gb - 5.0
                      and c["end"] - c["start"] >= MIN_CLIP_SEC]
            if not inside:
                continue
            best = max(inside, key=lambda c: _score(c, weights))
            pool.remove(best)
            length = min(best["end"] - best["start"], 90.0, remaining)
            s0, e0 = best["start"], best["start"] + length
            s1, e1 = _snap_start(s0, shot_cuts, silences), _snap_end(e0, shot_cuts, silences, video_dur)
            if e1 - s1 < MIN_CLIP_SEC:
                s1, e1 = s0, e0
            added.append({"act": _act_for_position(s1 / max(1.0, video_dur), False) + ": Bridge", "start": s1, "end": e1,
                          "title": f"Bridge scene at {format_seconds(s1)}", "dialogue": best.get("dialogue", "")[:600],
                          "importance": int(_score(best, weights)),
                          "reason": "Keeps the story connected: the most important dialogue in a long stretch that was cut out.",
                          "selected": True, "source": "bridge", "tier": 2, "tags": [], "filter_match": [],
                          "is_continuation": False, "exact_boundaries": False})
            remaining -= e1 - s1
            placed = True
            break
        if not placed:
            break
    return added


def compute_local_heuristic_summary(
    video_duration_sec: float,
    target_duration_sec: float,
    candidate_scenes: List[Dict[str, Any]],
    shot_cuts: List[float],
    silences: List[Tuple[float, float]],
    preset_key: str = "story_focused",
    spoiler_mode: str = "full_cut",
    target_character: Optional[str] = None,
    essence_milestones: Optional[List[Dict[str, Any]]] = None,
    flavor_segments: Optional[List[Dict[str, Any]]] = None,
    flavor_share: float = 0.4,
    filters: Optional[List[str]] = None,
    focus_names: Optional[List[str]] = None,
    dialogue_cues: Optional[List[Tuple[float, float]]] = None
) -> List[Dict[str, Any]]:
    """
    Story-arc budgeting. With essence milestones, every story beat gets a share of the requested
    duration proportional to its importance, filled from the best dialogue scenes near the beat.
    Without milestones, the film is split into equal story windows (proportional fallback).
    With flavor segments (songs, action, comedy, emotional scenes found anywhere in the film), up to
    `flavor_share` of the time goes to them and the story beats share the rest.
    The requested duration is honoured (within ~8%) unless the film itself is too short.
    """
    preset = PRESETS.get(preset_key, PRESETS["story_focused"])
    weights = dict(preset["weights"])
    has_dialogue = any(c.get("dialogue") and not c.get("synthetic") for c in candidate_scenes)
    if candidate_scenes and not has_dialogue:
        weights = {"dialogue": 0.05, "audio_dynamic": 0.45, "motion": 0.50, "coverage": 0.0}

    teaser = spoiler_mode == "teaser_spoiler_free"
    effective_max = video_duration_sec * (0.65 if teaser else 1.0)
    target = max(30.0, min(float(target_duration_sec), effective_max * 0.9))
    shot_cuts = shot_cuts or []
    silences = silences or []

    flavor: List[Dict[str, Any]] = []
    story_target = target
    if flavor_segments:
        flavor, used = _flavor_clips(flavor_segments, target, flavor_share, video_duration_sec, effective_max,
                                     teaser, shot_cuts, silences)
        story_target = max(target * 0.5, target - used)

    if essence_milestones:
        pool = [m for m in essence_milestones
                if not teaser or _beat_pos(m, video_duration_sec) <= 0.65] or essence_milestones
        chosen = select_beats(pool, story_target, filters, focus_names)
        beat_budget = story_target
        if has_dialogue and chosen and story_target / len(chosen) > BEAT_CAP_SEC:
            beat_budget = len(chosen) * BEAT_CAP_SEC
        scenes = _milestone_cut(chosen, video_duration_sec, effective_max, beat_budget, candidate_scenes,
                                shot_cuts, silences, weights, preset_key, teaser, target_character, filters, focus_names)
        if beat_budget < story_target:
            bridges = _bridge_scenes(scenes, candidate_scenes, story_target - beat_budget, weights, shot_cuts,
                                     silences, video_duration_sec, effective_max)
            scenes = _resolve_overlaps(scenes + bridges, video_duration_sec)
    else:
        scenes = _window_cut(video_duration_sec, effective_max, story_target, candidate_scenes, shot_cuts, silences,
                             weights, teaser, target_character)
    if flavor:
        scenes = _subtract_intervals(scenes, flavor)
        scenes = _resolve_overlaps(scenes + flavor, video_duration_sec)
    if sum(x["end"] - x["start"] for x in scenes) < target * 0.97:
        scenes = _top_up(scenes, target, effective_max)
    scenes = _fit_to_budget(scenes, target)
    scenes = _sentence_snap(scenes, dialogue_cues, video_duration_sec)
    return _trim_to_sentences(scenes, dialogue_cues, target)


def _milestone_cut(milestones, video_dur, effective_max, target, candidates, shot_cuts, silences,
                   weights, preset_key, teaser, target_character, filters=None,
                   focus_names=None) -> List[Dict[str, Any]]:
    active = [m for m in milestones if not teaser or m.get("target_percent", 0.5) <= 0.65]
    if not active:
        active = milestones[:max(3, len(milestones) // 2)]
    active = sorted(active, key=lambda m: float(m.get("target_time_sec", 0.0)))

    beat_weights = [float(m.get("importance", 90)) * _beat_priority(m, filters, focus_names) for m in active]
    total_w = sum(beat_weights) or 1.0

    anchors = []
    for m in active:
        t = float(m.get("target_time_sec", video_dur * float(m.get("target_percent", 0.5))))
        anchors.append(min(max(0.0, effective_max - 5.0), max(0.0, t)))

    scenes: List[Dict[str, Any]] = []
    for i, m in enumerate(active):
        budget = max(MIN_CLIP_SEC, target * beat_weights[i] / total_w)
        t = anchors[i]
        lo = 0.0 if i == 0 else (anchors[i - 1] + t) / 2.0
        hi = effective_max if i == len(active) - 1 else (t + anchors[i + 1]) / 2.0
        clips: List[Dict[str, Any]] = []

        if m.get("start_sec") is not None and m.get("end_sec") is not None:
            # Hand-timed beat from the essence database: keep its span, grow or trim it to the budget
            s0 = max(0.0, float(m["start_sec"]))
            e0 = min(video_dur, float(m["end_sec"]))
            if e0 - s0 < 1.0:
                e0 = min(video_dur, s0 + budget)
            exact = bool(m.get("exact", True))
            lo, hi = min(lo, s0), max(hi, e0)
            span = e0 - s0
            if budget >= span:
                extra = budget - span
                s = max(lo, s0 - 0.25 * extra)
                e = min(hi, e0 + extra - (s0 - s))
                if e - s < budget:
                    s = max(lo, e - budget)
            else:
                s, e = s0, s0 + budget
            clips.append({"start": s, "end": e, "dialogue": m.get("description", ""),
                          "snap_start": not (exact and abs(s - s0) < 0.01),
                          "snap_end": not (exact and abs(e - e0) < 0.01)})
        else:
            slack = max(90.0, budget * 1.5, video_dur * 0.05)
            w_start, w_end = max(lo, t - slack), min(hi, t + slack)
            if w_end - w_start < budget:
                w_start = max(0.0, min(w_start, t - budget))
                w_end = min(effective_max, max(w_end, w_start + budget))

            near = []
            for c in candidates:
                cs, ce = max(c["start"], w_start), min(c["end"], w_end)
                if ce - cs >= MIN_CLIP_SEC * 0.75:
                    near.append((_score(c, weights), cs, ce, c))
            near.sort(key=lambda x: -x[0])

            remaining = budget
            for score, cs, ce, c in near:
                if remaining < MIN_CLIP_SEC:
                    break
                length = min(ce - cs, remaining)
                ns, ne = cs, cs + length
                if any(ns < x["end"] and ne > x["start"] for x in clips):
                    continue
                clips.append({"start": ns, "end": ne, "dialogue": c.get("dialogue", ""),
                              "snap_start": True, "snap_end": True})
                remaining -= length
            clips.sort(key=lambda x: x["start"])

            if remaining >= MIN_CLIP_SEC and clips:
                for j, x in enumerate(clips):
                    if remaining < 1.0:
                        break
                    limit = clips[j + 1]["start"] if j + 1 < len(clips) else w_end
                    add = min(remaining, max(0.0, limit - x["end"]))
                    x["end"] += add
                    remaining -= add
                if remaining >= MIN_CLIP_SEC:
                    add = min(remaining, max(0.0, clips[0]["start"] - w_start))
                    clips[0]["start"] -= add
                    remaining -= add
            if not clips:
                s = max(lo, t - 0.3 * budget)
                e = min(max(hi, s + MIN_CLIP_SEC), s + budget, effective_max)
                clips.append({"start": s, "end": e, "dialogue": m.get("description", ""),
                              "snap_start": True, "snap_end": True})

        for k, x in enumerate(clips):
            s = _snap_start(x["start"], shot_cuts, silences) if x["snap_start"] else x["start"]
            e = _snap_end(x["end"], shot_cuts, silences, video_dur) if x["snap_end"] else x["end"]
            if e - s < 3.0:
                s, e = x["start"], x["end"]
            first = k == 0
            title = m.get("title", f"Beat {i + 1}") + ("" if first else " (continued)")
            dialogue = x.get("dialogue") or m.get("description", "")
            if target_character and target_character.lower() in dialogue.lower():
                title += f" ({target_character} POV)"
            scenes.append({
                "act": m.get("act", f"Act {i + 1}"),
                "start": s,
                "end": e,
                "title": title,
                "dialogue": dialogue[:600],
                "importance": int(m.get("importance", 90)),
                "reason": m.get("description", f"Story beat: {title}") + _match_note(m, filters, focus_names),
                "tags": beat_tags(m),
                "filter_match": sorted(set(beat_tags(m)) & set(filters or [])),
                "tier": int(m.get("tier", 1) or 1),
                "voiceover_text": (m.get("narration_en") or m.get("narration_hi") or m.get("description")) if first else None,
                "narration_hi": m.get("narration_hi") if first else None,
                "narration_en": m.get("narration_en") if first else None,
                "selected": True,
                "source": "canonical_essence",
                "milestone_id": m.get("id", i + 1),
                "is_continuation": not first,
                "exact_boundaries": not x["snap_start"] and not x["snap_end"]
            })

    return _resolve_overlaps(scenes, video_dur)


def _window_cut(video_dur, effective_max, target, candidates, shot_cuts, silences, weights, teaser,
                target_character) -> List[Dict[str, Any]]:
    num_windows = max(5, min(12, int(target / 150)))
    window_duration = effective_max / num_windows
    budget = target / num_windows

    if teaser:
        act_names = ["Act 1: Prologue & Setup", "Act 1: Inciting Incident", "Act 2A: Rising Action",
                     "Act 2A: The Escalation", "Act 2B: Midpoint Revelation", "Act 2B: The Turning Point"]
    else:
        act_names = ["Act 1: Prologue & Setup", "Act 1: Inciting Incident", "Act 2A: Rising Action",
                     "Act 2A: First Confrontation", "Act 2B: Midpoint Revelation", "Act 2B: Escalating Stakes",
                     "Act 2B: The Darkest Hour", "Act 3: Preparation & Mobilization"]

    scenes: List[Dict[str, Any]] = []
    for w_idx in range(num_windows):
        w_start = w_idx * window_duration
        w_end = min(effective_max, (w_idx + 1) * window_duration)
        if teaser and w_idx == num_windows - 1:
            act_name = "Act 2: The Cliffhanger (Ending Preserved)"
        elif not teaser and w_idx == num_windows - 1:
            act_name = "Act 3: Epilogue & Resolution"
        elif not teaser and w_idx == num_windows - 2:
            act_name = "Act 3: The Climax"
        else:
            act_name = act_names[min(w_idx, len(act_names) - 1)]

        window = []
        for c in candidates:
            cs, ce = max(c["start"], w_start), min(c["end"], w_end)
            if ce - cs >= MIN_CLIP_SEC * 0.75:
                window.append((_score(c, weights), cs, ce, c))
        window.sort(key=lambda x: -x[0])

        picked = []
        remaining = budget
        for score, cs, ce, c in window:
            if remaining < MIN_CLIP_SEC:
                break
            length = min(ce - cs, max(remaining, MIN_CLIP_SEC), max(DEFAULT_MAX_SCENE_SEC, budget))
            if any(cs < p[1] and cs + length > p[0] for p in picked):
                continue
            picked.append((cs, cs + length, c.get("dialogue", ""), score))
            remaining -= length

        if not picked:
            length = min(budget, window_duration * 0.8)
            start = w_start + (window_duration - length) / 2.0
            picked.append((start, start + length, "[Visual Narrative / Atmospheric Scene]", 50.0))

        for cs, ce, dialogue, score in sorted(picked):
            s = _snap_start(cs, shot_cuts, silences)
            e = _snap_end(ce, shot_cuts, silences, video_dur)
            if e - s < 3.0:
                s, e = cs, ce
            title = f"Scene {len(scenes) + 1}"
            if target_character and target_character.lower() in dialogue.lower():
                title += f" ({target_character} POV)"
            scenes.append({
                "act": act_name,
                "start": s,
                "end": e,
                "title": title,
                "dialogue": dialogue[:600],
                "importance": int(score),
                "reason": f"Key story beat in {act_name}",
                "selected": True,
                "source": "heuristic"
            })

    return _resolve_overlaps(scenes, video_dur)


def fit_and_snap(scenes: List[Dict[str, Any]], cues: Optional[List[Tuple[float, float]]], target: float,
                 video_dur: float) -> List[Dict[str, Any]]:
    """For cuts made elsewhere (lecture engine, AI Director): reach the requested length, then move every
    clip edge to a line break and trim whole lines if that pushed the cut over the length."""
    if not scenes:
        return scenes
    if sum(x["end"] - x["start"] for x in scenes) < target * 0.95:
        scenes = _top_up(scenes, target, video_dur)
    scenes = _fit_to_budget(scenes, target)
    scenes = _sentence_snap(scenes, cues, video_dur)
    return _trim_to_sentences(scenes, cues, target)
