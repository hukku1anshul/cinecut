"""
Gap narration.

Each narration line is spoken at the start of a kept scene and describes ONLY the part of the film that
was cut out just before it (between the previous kept scene and this one) - never earlier or later events.

How a line is produced, in order:
  1. AI summary of the dialogue inside that gap only (any free provider, then the local model). Written in
     English first and checked (short, no names missing from the gap's dialogue, no "later in the film"),
     then translated if another language was chosen.
  2. A story beat (from the essence database or the AI's transcript beats) that falls inside the gap.
  3. For films without subtitles: the AI's knowledge of the film, told what the previous and next kept
     scenes are and asked only about the stretch between them. It may say it does not know.
  4. A neutral time skip ("The story moves ahead about 12 minutes."), which is always true.
"""
import re
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple, Callable

from backend.video_engine import llm_client
from backend.video_engine.flavor_detector import ANY_TAG
from backend.video_engine.narrator import INTRO_TEMPLATES
from backend.video_engine.probe import format_seconds

MIN_GAP_SEC = 25.0
_ENGLISH_CACHE: Dict[tuple, str] = {}
MAX_WORDS = 24

TIME_SKIP = {
    "English": "The story moves ahead about {m} minutes.",
    "Hindi": "कहानी लगभग {m} मिनट आगे बढ़ती है।",
    "Spanish": "La historia avanza unos {m} minutos.",
    "French": "L'histoire avance d'environ {m} minutes.",
    "German": "Die Geschichte springt etwa {m} Minuten weiter.",
    "Japanese": "物語は約{m}分先へ進みます。",
}
LECTURE_SKIP = {
    "English": "We skip about {m} minutes of the lecture{topic}.",
    "Hindi": "व्याख्यान के लगभग {m} मिनट छोड़े जा रहे हैं{topic}।",
}
BANNED_PHRASES = {
    "English": ["previously", "earlier in the film", "later in the film", "next scene", "previous scene",
                "in the end", "eventually", "little do they know", "flashback", "earlier in the lecture",
                "later in the lecture", "i don't know", "i am not sure", "i'm not sure"],
    "Hindi": ["पिछले दृश्य", "अगले दृश्य", "आखिर में", "अंत में", "बाद में पता"],
}
LATIN_LANGS = {"English", "Spanish", "French", "German"}
SCRIPT_NAMES = {"Hindi": "Devanagari", "Japanese": "Japanese"}
COMMON_CAPS = {"I", "The", "They", "He", "She", "It", "We", "His", "Her", "Their", "Meanwhile", "Later", "Then",
               "Mr", "Mrs", "Ms", "Dr", "Sir", "Madam"}

SUMMARY_PROMPT = """You are the narrator of a condensed version of {kind} "{title}".
The viewer has just watched one kept scene. Before the next kept scene starts, about {minutes} minutes were cut out.
Below is the dialogue from ONLY that cut-out part.

Write {count} in English that briefly name the {what} of the cut-out part (who does what, and why it
matters), so the viewer can follow along. Mention only the most important things.
Rules:
- Ignore song lyrics; describe only what the characters do and say.
- Write every name in English letters.
{names_rule}- Use only facts stated or clearly implied in this dialogue.
- Do not mention anything that happens before or after this part: no foreshadowing and no recap of earlier events.
- Do not invent names, places or events. If a person's name is not in the dialogue, describe the person instead.
- Do not quote the dialogue; use your own words, in the present tense.
- At most 24 words in total.
- If the dialogue is too unclear to summarize, return an empty string.
{retry}
Dialogue from the cut-out part:
---
{transcript}
---
Return JSON: {{"narration": "..."}}"""

KNOWLEDGE_PROMPT = """You narrate a condensed version of the film "{title}".
In the condensed version the viewer has just watched: {prev}.
The next kept scene is: {next}.
About {minutes} minutes of the film between those two scenes were cut out (from {a} to {b} of a {runtime}-minute film).

If you know this film, write {count} in English that briefly name the main events of that cut-out stretch,
so the story still makes sense.
Rules:
- Describe only events between those two scenes. Do not describe the next scene itself or anything after it,
  and do not repeat the previous scene.
- Present tense, each sentence under 20 words, your own words.
{names_rule}- If you do not know this film well enough to say what happens in that stretch, return an empty string.
Return JSON: {{"narration": "..."}}"""

TRANSLATE_PROMPT = """Translate this narration line into natural, simple spoken {language} ({script} script), as a
film narrator would say it. Keep the meaning exactly. Keep people's names, written in {script} script.
Do not add or remove any information.

Narration: {line}

Return JSON: {{"narration": "..."}}"""


TRANSLATE_BATCH_PROMPT = """Translate each numbered narration line into natural, simple spoken {language} ({script} script),
as a film narrator would say it. Keep the meaning of each line exactly; do not add or remove information.
Write every person's name the same way in every line, in {script} script.

Lines:
{lines}

Return JSON: {{"lines": ["...", "..."]}} with exactly {n} items, in the same order."""


def _latin_ok(text: str, english: Optional[str]) -> bool:
    """English words inside a Hindi line are fine only when they come from the English line (MIT, Big O, DNA)."""
    words = re.findall("[A-Za-z]{3,}", text)
    if not words:
        return True
    return bool(english) and all(w.lower() in english.lower() for w in words)


def _translation_ok(text: str, language: str, english: Optional[str] = None) -> bool:
    lower = text.lower()
    mixed_script = language in SCRIPT_NAMES and not _latin_ok(text, english)
    return bool(text) and not mixed_script and _script_ok(text, language) and \
        not any(p in lower for p in BANNED_PHRASES.get(language, []))


def _translate_batch(lines: List[str], language: str, api_key: Optional[str]) -> List[Optional[str]]:
    """All lines in one request, so names are spelled the same way throughout the narration."""
    script = SCRIPT_NAMES.get(language, "Latin")
    numbered = "\n".join(f"{n + 1}. {line}" for n, line in enumerate(lines))
    try:
        data = llm_client.generate_json(TRANSLATE_BATCH_PROMPT.format(language=language, script=script, lines=numbered,
                                                                      n=len(lines)),
                                        api_key=api_key, temperature=0.1, timeout=180,
                                        ollama_model_name=llm_client.translation_model())
    except Exception:
        return [None] * len(lines)
    items = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != len(lines):
        return [None] * len(lines)
    out: List[Optional[str]] = []
    for item, source in zip(items, lines):
        text = _clean_line(re.sub(r"^\s*\d+[.)]\s*", "", str(item or "")), max_words=45)
        out.append(text if _translation_ok(text, language, source) else None)
    return out


def gap_transcript(subtitles: List[Dict[str, Any]], a: float, b: float, max_chars: int = 6000) -> Tuple[str, int]:
    """Dialogue lines that lie entirely inside [a, b], caption tags removed. Returns (text, word_count)."""
    lines: List[str] = []
    for s in subtitles or []:
        if s["start"] >= a - 0.5 and s["end"] <= b + 0.5:
            t = " ".join(ANY_TAG.sub(" ", s.get("text", "")).split())
            if t and (not lines or lines[-1] != t):
                lines.append(t)
    words = sum(len(l.split()) for l in lines)
    text = "\n".join(lines)
    if len(text) > max_chars:
        third = max_chars // 3
        mid = len(text) // 2
        text = text[:third] + "\n...\n" + text[mid - third // 2: mid + third // 2] + "\n...\n" + text[-third:]
    return text, words


def _script_ok(text: str, language: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    deva = sum(1 for ch in letters if "ऀ" <= ch <= "ॿ")
    if language == "Hindi":
        return deva / len(letters) > 0.6
    if language in LATIN_LANGS:
        return deva == 0 and all(ord(ch) < 0x2000 for ch in letters)
    return True


def _unknown_names(text: str, transcript: str, title: str) -> List[str]:
    source = (transcript + " " + title).lower()
    unknown = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        for w in sentence.split()[1:]:
            token = re.sub(r"[^A-Za-z']", "", w).strip("'")
            if len(token) > 2 and token[0].isupper() and token not in COMMON_CAPS and token.lower() not in source:
                unknown.append(token)
    return unknown


def _clean_line(text: str, max_words: int = MAX_WORDS) -> str:
    text = " ".join(str(text or "").replace('"', "").split())
    sentences = [s for s in re.split(r"(?<=[.!?।])\s+", text) if s.strip()]
    kept: List[str] = []
    for s in sentences[:2]:
        if kept and len(" ".join(kept + [s]).split()) > max_words:
            break
        kept.append(s)
    line = " ".join(kept)
    words = line.split()
    if len(words) > max_words:
        line = " ".join(words[:max_words]).rstrip(",;:") + "."
    return line


def _check_english(line: str, transcript: Optional[str], title: str, transcript_is_latin: bool) -> Optional[str]:
    if not line:
        return "the answer was empty"
    if not _script_ok(line, "English"):
        return "it was not written in English"
    lower = line.lower()
    for phrase in BANNED_PHRASES["English"]:
        if phrase in lower:
            return f"it referred to events outside the cut-out part ('{phrase}')"
    if transcript is not None and transcript_is_latin:
        unknown = _unknown_names(line, transcript, title)
        if unknown:
            return f"it used names that are not in the dialogue ({', '.join(unknown[:3])})"
    return None


def _translate(line: str, language: str, api_key: Optional[str]) -> Optional[str]:
    script = SCRIPT_NAMES.get(language, "Latin")
    for _ in range(2):
        try:
            data = llm_client.generate_json(TRANSLATE_PROMPT.format(language=language, script=script, line=line),
                                            api_key=api_key, temperature=0.1, timeout=90,
                                            ollama_model_name=llm_client.translation_model())
        except Exception:
            return None
        out = _clean_line(data.get("narration", "") if isinstance(data, dict) else "", max_words=55)
        if out and _translation_ok(out, language, line):
            return out
    return None


def _finish(english: str, language: str, api_key: Optional[str]) -> Optional[str]:
    return english if language == "English" else _translate(english, language, api_key)


def _names_rule(names: Optional[List[str]]) -> str:
    names = [n for n in (names or []) if n and not re.search("[\u0900-\u097F]", n)][:12]
    return ("- When these people appear, spell their names exactly like this: " + ", ".join(names) + ".\n") if names else ""


def _ai_line(transcript: str, minutes: int, title: str, language: str, api_key: Optional[str],
             content_mode: str, names: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str]]:
    kind = "the lecture" if content_mode == "lecture" else "the film"
    count = "one short sentence" if minutes < 5 else "one or two short sentences"
    latin = not re.search("[ऀ-ॿ]", transcript)
    cached = _ENGLISH_CACHE.get(("t", title, transcript[:80], len(transcript)))
    if cached:
        final = _finish(cached, language, api_key)
        return (final, None) if final else (None, f"translation to {language} failed")
    retry, problem = "", None
    for _ in range(2):
        prompt = SUMMARY_PROMPT.format(kind=kind, title=title, minutes=minutes, count=count, retry=retry,
                                       transcript=transcript,
                                       what="main topics" if content_mode == "lecture" else "main events",
                                       names_rule=_names_rule(names))
        try:
            data = llm_client.generate_json(prompt, api_key=api_key, temperature=0.2, timeout=120)
        except Exception as e:
            return None, f"AI unavailable: {e}"
        candidate = _clean_line(data.get("narration", "") if isinstance(data, dict) else "")
        problem = _check_english(candidate, transcript, title, latin)
        if not problem:
            _ENGLISH_CACHE[("t", title, transcript[:80], len(transcript))] = candidate
            final = _finish(candidate, language, api_key)
            return (final, None) if final else (None, f"translation to {language} failed")
        retry = f"\nYour previous answer was rejected because {problem}. Follow the rules exactly.\n"
    return None, problem


def _scene_label(scene: Optional[Dict[str, Any]]) -> str:
    if not scene:
        return "the opening of the film"
    title = scene.get("title", "a scene")
    about = scene.get("reason") or scene.get("dialogue") or ""
    about = about if about and not about.startswith(("Key story beat", "Picked for your flavor")) else ""
    return f'"{title}"' + (f" ({about[:200]})" if about else "")


_STEM_STOP = {"their", "there", "about", "after", "before", "which", "while", "where", "other",
              "these", "those", "being", "under", "again", "story", "scene", "meanwhile", "finally"}


def _stems(text: str) -> set:
    return {w[:5] for w in re.findall("[a-z]{5,}", (text or "").lower()) if w not in _STEM_STOP}


def _future_words(line: str, earlier_text: str, later_text: str) -> List[str]:
    """Words in the line that only appear in LATER scenes/beats (e.g. 'kidnap' before the kidnapping)."""
    distinctive = _stems(later_text) - _stems(earlier_text)
    return sorted(_stems(line) & distinctive)


def _knowledge_line(title: str, prev_scene: Optional[Dict[str, Any]], next_scene: Dict[str, Any], a: float, b: float,
                    runtime: float, language: str, api_key: Optional[str], earlier_text: str = "",
                    later_text: str = "", names: Optional[List[str]] = None) -> Optional[str]:
    key = ("k", title, round(a), round(b))
    if key in _ENGLISH_CACHE:
        return _finish(_ENGLISH_CACHE[key], language, api_key)
    minutes = max(1, int(round((b - a) / 60)))
    prompt = KNOWLEDGE_PROMPT.format(title=title, prev=_scene_label(prev_scene), next=_scene_label(next_scene),
                                     minutes=minutes, a=format_seconds(a), b=format_seconds(b),
                                     runtime=int(runtime // 60) if runtime else "?",
                                     count="one short sentence" if minutes < 5 else "one or two short sentences",
                                     names_rule=_names_rule(names))
    retry = ""
    for _ in range(2):
        try:
            data = llm_client.generate_json(prompt + retry, api_key=api_key, temperature=0.2, timeout=120)
        except Exception:
            return None
        candidate = _clean_line(data.get("narration", "") if isinstance(data, dict) else "")
        if not candidate:
            return None  # the model says it does not know this stretch
        if _check_english(candidate, None, title, False):
            retry = "\nYour previous answer broke the rules. Write it again following every rule."
            continue
        future = _future_words(candidate, earlier_text, later_text) if later_text else []
        if future:
            # it described something that happens later in the film: ask once more, naming what to leave out
            retry = ("\nYour previous answer was: " + candidate + "\nIt mentioned events that happen later in the film "
                     "(words like: " + ", ".join(future[:6]) + "). Describe only the cut-out stretch.")
            continue
        _ENGLISH_CACHE[key] = candidate
        return _finish(candidate, language, api_key)
    return None


def _milestone_line(milestones: Optional[List[Dict[str, Any]]], a: float, b: float, language: str) -> Optional[str]:
    # Only beats well inside the cut-out stretch: a beat within 60 s of the next kept scene is that scene itself
    inside = [m for m in (milestones or []) if a + 5 < float(m.get("target_time_sec", -1)) < b - 60]
    if not inside:
        return None
    best = max(inside, key=lambda m: m.get("importance", 0))
    if language == "Hindi" and best.get("narration_hi"):
        return best["narration_hi"]
    if language == "English":
        return best.get("narration_en") or best.get("description")
    return None


def _lecture_topics(transcript: str) -> str:
    from backend.video_engine.lecture_engine import _tokens
    words = [w for w, _ in Counter(_tokens(transcript)).most_common(3)]
    return (" on " + ", ".join(words)) if words else ""


BATCH_PROMPT = """You are the narrator of a condensed version of {kind} "{title}".
Several parts of it were cut out. For EACH numbered part below you get only the dialogue from that part.
For each part, write one or two short sentences in English that briefly name the {what} of that part (who does what,
and why it matters), so the viewer can follow along. Mention only the most important things.
Rules for every line:
- Use only facts stated or clearly implied in that part's own dialogue; nothing from the other parts.
- No foreshadowing and no recap of earlier events.
- Do not invent names. If a person's name is not in that part's dialogue, describe the person instead.
- Ignore song lyrics; describe only what the characters do and say.
- Write every name in English letters.
{names_rule}- Present tense, your own words, at most 24 words per part in total.
- If a part is too unclear to summarize, give an empty string for it.

{parts}

Return JSON: {{"lines": [{{"part": 1, "narration": "..."}}]}} with one entry per part."""

VISUAL_PROMPT = """You are the narrator of a condensed version of the film "{title}". About {minutes} minutes were cut out
here. There is little dialogue in that part; this is what is seen on screen in it:
{visuals}
Write one or two short sentences in English that briefly say what happens in that part, using only these descriptions.
No foreshadowing, no names that are not in the descriptions, present tense, each sentence under 20 words.
{names_rule}Return JSON: {{"narration": "..."}}"""

BATCH_CHARS = 16000


def _ai_lines_batch(items: List[Dict[str, Any]], title: str, api_key: Optional[str], content_mode: str,
                    names: Optional[List[str]]) -> Dict[int, str]:
    """Writes the lines for many cut-out parts in a few requests. Returns {scene index: accepted English line}."""
    kind = "the lecture" if content_mode == "lecture" else "the film"
    what = "main topics" if content_mode == "lecture" else "main events"
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    size = 0
    for it in items:
        if cur and (len(cur) >= 8 or size + len(it["short"]) > BATCH_CHARS):
            groups.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(it["short"])
    if cur:
        groups.append(cur)
    out: Dict[int, str] = {}

    def run(group):
        blocks = "\n\n".join(f"Part {n} (about {it['minutes']} minutes cut out):\n---\n{it['short']}\n---"
                             for n, it in enumerate(group, 1))
        prompt = BATCH_PROMPT.format(kind=kind, title=title, what=what, names_rule=_names_rule(names), parts=blocks)
        try:
            return group, llm_client.generate_json(prompt, api_key=api_key, temperature=0.2, timeout=240, num_ctx=16384)
        except Exception:
            return group, None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(3, len(groups) or 1)) as pool:
        answers = list(pool.map(run, groups))
    for group, data in answers:
        if data is None:
            continue
        rows = data.get("lines") if isinstance(data, dict) else data if isinstance(data, list) else []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("part", 0))
            except (TypeError, ValueError):
                continue
            if not 1 <= n <= len(group):
                continue
            it = group[n - 1]
            cand = _clean_line(row.get("narration", ""))
            if cand and not _check_english(cand, it["full"], title, it["latin"]):
                out[it["i"]] = cand
                _ENGLISH_CACHE[("t", title, it["full"][:80], len(it["full"]))] = cand
    return out


def _visual_line(seen: List[Dict[str, Any]], minutes: int, title: str, api_key: Optional[str],
                 names: Optional[List[str]]) -> Optional[str]:
    desc = "\n".join(f"- {v['description']}" for v in seen[:6])
    key = ("v", title, desc[:80], len(desc))
    if key in _ENGLISH_CACHE:
        return _ENGLISH_CACHE[key]
    try:
        data = llm_client.generate_json(VISUAL_PROMPT.format(title=title, minutes=minutes, visuals=desc,
                                                             names_rule=_names_rule(names)),
                                        api_key=api_key, temperature=0.2, timeout=120)
    except Exception:
        return None
    cand = _clean_line(data.get("narration", "") if isinstance(data, dict) else "")
    if cand and not _check_english(cand, desc, title, True):
        _ENGLISH_CACHE[key] = cand
        return cand
    return None


def generate_gap_narrations(
    scenes: List[Dict[str, Any]],
    subtitles: List[Dict[str, Any]],
    movie_title: str,
    language: str = "English",
    api_key: Optional[str] = None,
    milestones: Optional[List[Dict[str, Any]]] = None,
    content_mode: str = "movie",
    progress: Optional[Callable[[int, int], None]] = None,
    use_ai: bool = True,
    video_duration: Optional[float] = None,
    prefer_handwritten: bool = False,
    names: Optional[List[str]] = None,
    visuals: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Writes `bridge_narration` on the scenes (in place). Returns counts per source."""
    order = sorted([i for i, s in enumerate(scenes) if s.get("selected", True)], key=lambda i: scenes[i]["start"])
    for s in scenes:
        s.update({"bridge_narration": None, "narration_source": None, "narration_covers": None})
    provider = llm_client.provider_for(api_key) if use_ai else None
    stats: Dict[str, Any] = {"handwritten": 0, "ai": 0, "ai_visual": 0, "ai_knowledge": 0, "database": 0, "time_skip": 0,
                             "intro": 0, "none": 0, "provider": llm_client.describe(api_key) if provider else "rule-based only",
                             "rejected": 0}
    runtime = video_duration or (max(s["end"] for s in scenes) if scenes else 0)
    batch = language != "English" and provider is not None
    work = "English" if batch else language
    to_translate: List[Tuple[int, str]] = []
    entries: List[Dict[str, Any]] = []

    # 1. Which scenes get a line, and what was cut out before each
    prev_end, prev_scene = 0.0, None
    for k, i in enumerate(order):
        s = scenes[i]
        a, b = prev_end, s["start"]
        gap = b - a
        this_prev = prev_scene
        prev_end, prev_scene = max(prev_end, s["end"]), s
        cover = {"start": round(a, 1), "end": round(b, 1), "start_formatted": format_seconds(a), "end_formatted": format_seconds(b)}
        hand = None
        if prefer_handwritten and not s.get("is_continuation"):
            hand = s.get("narration_hi") if language == "Hindi" else (s.get("narration_en") if language == "English" else None)
        if hand:
            s.update({"bridge_narration": hand, "narration_source": "handwritten", "narration_covers": cover})
            stats["handwritten"] += 1
            continue
        if k == 0 and gap < 90.0:
            s.update({"bridge_narration": INTRO_TEMPLATES.get(language, INTRO_TEMPLATES["English"]).format(title=movie_title),
                      "narration_source": "intro"})
            stats["intro"] += 1
            continue
        if gap < MIN_GAP_SEC:
            stats["none"] += 1
            continue
        full, words = gap_transcript(subtitles, a, b)
        short, _ = gap_transcript(subtitles, a, b, max_chars=2400)
        entries.append({"i": i, "a": a, "b": b, "minutes": max(1, int(round(gap / 60.0))), "full": full, "short": short,
                        "words": words, "latin": not re.search("[\u0900-\u097F]", full), "prev": this_prev, "cover": cover})

    # 2. Lines from the cut-out dialogue, written for many parts per request
    batched: Dict[int, str] = {}
    if provider:
        todo = []
        for e in entries:
            if e["words"] < 25:
                continue
            cached = _ENGLISH_CACHE.get(("t", movie_title, e["full"][:80], len(e["full"])))
            if cached:
                batched[e["i"]] = cached
            else:
                todo.append(e)
        if todo:
            batched.update(_ai_lines_batch(todo, movie_title, api_key, content_mode, names))
        stats["written_in_batches"] = len(batched)

    # 3. Fill every line, falling back step by step
    for n, e in enumerate(entries):
        if progress:
            progress(n, len(entries))
        i, s = e["i"], scenes[e["i"]]
        line, source = None, None
        if provider and e["words"] >= 25:
            line = batched.get(i)
            if not line:
                line, problem = _ai_line(e["full"], e["minutes"], movie_title, work, api_key, content_mode, names)
                if not line and problem and "unavailable" not in problem:
                    stats["rejected"] += 1
            source = "ai" if line else None
        if not line and content_mode == "movie":
            line = _milestone_line(milestones, e["a"], e["b"], language)
            if not line and batch:
                line = _milestone_line(milestones, e["a"], e["b"], "English")
                if line:
                    to_translate.append((i, line))
            source = "database" if line else None
        if not line and provider and content_mode == "movie" and e["words"] < 25:
            seen = [v for v in (visuals or []) if v["start"] >= e["a"] - 1 and v["end"] <= e["b"] + 1]
            if seen:
                line = _visual_line(seen, e["minutes"], movie_title, api_key, names)
                source = "ai_visual" if line else None
        if not line and provider and content_mode == "movie" and e["words"] < 25:
            earlier = " ".join((x.get("title", "") + " " + (x.get("reason") or "")) for x in scenes
                               if x.get("selected", True) and x["start"] <= s["start"])
            later = " ".join((x.get("title", "") + " " + (x.get("reason") or "")) for x in scenes
                             if x.get("selected", True) and x["start"] > s["start"])
            later += " " + " ".join((m.get("title", "") + " " + m.get("description", "")) for m in (milestones or [])
                                      if float(m.get("target_time_sec", 0)) > s["end"])
            line = _knowledge_line(movie_title, e["prev"], s, e["a"], e["b"], runtime, work, api_key, earlier, later, names)
            source = "ai_knowledge" if line else None
        if not line:
            if content_mode == "lecture":
                template = LECTURE_SKIP.get(language, LECTURE_SKIP["English"])
                line = template.format(m=e["minutes"], topic=_lecture_topics(e["full"]) if language == "English" else "")
            else:
                line = TIME_SKIP.get(language, TIME_SKIP["English"]).format(m=e["minutes"])
            source = "time_skip"
        stats[source] += 1
        if batch and source in ("ai", "ai_knowledge", "ai_visual"):
            to_translate.append((i, line))
        s.update({"bridge_narration": line, "narration_source": source, "narration_covers": e["cover"]})

    # 4. Other languages: translate all lines in one request (names stay consistent)
    if to_translate:
        done = _translate_batch([t for _, t in to_translate], language, api_key)
        stats["translated_in_one_request"] = sum(1 for d in done if d)
        for (i, english), final in zip(to_translate, done):
            sc = scenes[i]
            final = final or _translate(english, language, api_key)
            if not final:
                cov = sc.get("narration_covers") or {}
                minutes = max(1, int(round((cov.get("end", 0) - cov.get("start", 0)) / 60.0)))
                if content_mode == "lecture":
                    final = LECTURE_SKIP.get(language, LECTURE_SKIP["English"]).format(m=minutes, topic="")
                else:
                    final = TIME_SKIP.get(language, TIME_SKIP["English"]).format(m=minutes)
                stats[sc["narration_source"]] -= 1
                stats["time_skip"] += 1
                sc["narration_source"] = "time_skip"
            sc["bridge_narration"] = final
            sc["narration_english"] = english
    return stats
