"""
Finds the kinds of scenes a viewer asks for with a flavor profile, anywhere in the film:
  - songs:     long stretches of continuous music ([Music]/[संगीत] caption tags, or no dialogue
               pauses with steady loudness when there are no captions)
  - action:    fast cutting + loud audio + little dialogue
  - comedy:    [Laughter]/[हंसी] caption tags, or rapid back-and-forth dialogue
  - emotional: slow cutting, quieter audio, dialogue present
Each detector works on 10-second windows built from captions, the loudness profile, dialogue pauses
and shot changes, and returns scored segments. The summarizer reserves part of the time budget for them.
"""
import re
from bisect import bisect_left
from typing import List, Dict, Any, Optional, Tuple

from backend.video_engine.audio_analyzer import EnergyIndex

WINDOW = 10.0
MUSIC_TAG = re.compile(r"\[(?:music|संगीत|song|गाना|गीत)\]|♪|♫", re.IGNORECASE)
LAUGH_TAG = re.compile(r"\[(?:laughter|laughs|laughing|हंसी|हँसी|ठहाके)\]", re.IGNORECASE)
ANY_TAG = re.compile(r"\[[^\]]*\]|♪|♫")

FLAVOR_FOR_PRESET = {
    "musical_romance": "song",
    "action_energy": "action",
    "comedy_fun": "comedy",
    "emotional_drama": "emotional",
}
FLAVOR_LABELS = {"song": "Song sequence", "action": "Action sequence", "comedy": "Comedy scene",
                 "emotional": "Emotional scene"}
MIN_LEN = {"song": 70.0, "action": 20.0, "comedy": 20.0, "emotional": 30.0}
MAX_CLIP = {"song": 120.0, "action": 75.0, "comedy": 60.0, "emotional": 75.0}


def _windows(subtitles, energy: EnergyIndex, shot_cuts, silences, duration: float) -> List[Dict[str, Any]]:
    n = max(1, int(duration // WINDOW))
    wins = [{"start": i * WINDOW, "end": min(duration, (i + 1) * WINDOW), "words": 0, "lines": 0,
             "music": 0, "laugh": 0, "punct": 0} for i in range(n)]
    for s in subtitles or []:
        i = min(n - 1, int(s["start"] // WINDOW))
        text = s.get("text", "")
        wins[i]["music"] += len(MUSIC_TAG.findall(text))
        wins[i]["laugh"] += len(LAUGH_TAG.findall(text))
        spoken = ANY_TAG.sub(" ", text).strip()
        if spoken:
            wins[i]["words"] += len(spoken.split())
            wins[i]["lines"] += 1
            wins[i]["punct"] += spoken.count("?") + spoken.count("!")
    film_cut_rate = len(shot_cuts) / duration if shot_cuts and duration else 0.0
    for w in wins:
        a, b = w["start"], w["end"]
        w["loud"] = energy.percentile(a, b)
        cuts = bisect_left(shot_cuts, b) - bisect_left(shot_cuts, a) if shot_cuts else 0
        w["pace"] = (cuts / (b - a)) / film_cut_rate if film_cut_rate > 0 else 1.0
        pause = 0.0
        for s0, s1 in silences or []:
            if s1 <= a:
                continue
            if s0 >= b:
                break
            pause += min(b, s1) - max(a, s0)
        w["pause_frac"] = pause / max(1.0, b - a)
    return wins


def _merge(flags: List[bool], wins, allow_gap: int = 1) -> List[Tuple[int, int]]:
    runs, start, gap = [], None, 0
    for i, f in enumerate(flags):
        if f:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap > allow_gap:
                runs.append((start, i - gap))
                start, gap = None, 0
    if start is not None:
        runs.append((start, len(flags) - 1 - gap))
    return runs


def detect_flavor_segments(flavor: str, subtitles, energy_values, shot_cuts, silences,
                           duration: float) -> List[Dict[str, Any]]:
    """Returns segments [{start, end, score, flavor, label, evidence}] sorted by score (best first)."""
    if flavor not in MIN_LEN or duration <= 0:
        return []
    energy = EnergyIndex(energy_values or [])
    wins = _windows(subtitles, energy, shot_cuts or [], silences or [], duration)
    has_captions = bool(subtitles)
    if flavor == "song":
        flags = [(w["music"] > 0 and w["words"] < 25) or
                 (w["pause_frac"] < 0.05 and w["loud"] >= 40 and (w["words"] < 12 if has_captions else True))
                 for w in wins]
    elif flavor == "action":
        flags = [w["pace"] >= 1.6 and w["loud"] >= 60 and w["words"] < 20 for w in wins]
    elif flavor == "comedy":
        if any(w["laugh"] for w in wins):
            flags = [w["laugh"] > 0 for w in wins]
        else:
            flags = [w["lines"] >= 4 and w["punct"] >= 2 for w in wins]
    else:  # emotional
        flags = [w["pace"] <= 0.6 and w["loud"] <= 50 and (w["words"] >= 8 if has_captions else w["pause_frac"] > 0.2)
                 for w in wins]

    segments = []
    for a, b in _merge(flags, wins, allow_gap=2 if flavor == "song" else 1):
        start, end = wins[a]["start"], wins[b]["end"]
        if end - start < MIN_LEN[flavor]:
            continue
        block = wins[a:b + 1]
        loud = sum(w["loud"] for w in block) / len(block)
        pace = sum(w["pace"] for w in block) / len(block)
        tags = sum(w["music"] + w["laugh"] for w in block)
        if flavor == "song":
            score, evidence = min(100, 40 + (end - start) / 6 + loud / 4), ("music captions" if tags else "continuous music")
        elif flavor == "action":
            score, evidence = min(100, 30 * min(pace, 3) + loud / 2), "fast cutting and loud audio"
        elif flavor == "comedy":
            score, evidence = min(100, 50 + 5 * tags + (end - start) / 4), ("laughter captions" if tags else "quick dialogue")
        else:
            score, evidence = min(100, 40 + (100 - loud) / 3 + (end - start) / 6), "slow, quiet dialogue"
        segments.append({"start": start, "end": end, "score": round(score, 1), "flavor": flavor,
                         "label": FLAVOR_LABELS[flavor], "evidence": evidence})
    segments.sort(key=lambda s: -s["score"])
    return segments
