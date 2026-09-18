"""
Own-words summaries ("explainers") for books, lectures and stories.

Instead of re-cutting someone else's footage, CineCut reads the material, works out its essence and retells it in
new words, with its own slides and narration:
  1. Read in parts: each part (about 3,500 words) gives its ideas (books, lectures) or events (stories), each with
     why it matters and, for ideas, a fresh example of our own. About three parts go in one request.
  2. Read as a whole: one pass turns all the notes into an outline sized to the requested length (about 140 spoken
     words a minute in English, 120 in Hindi): a one-line essence, sections, takeaways, a glossary and a quiz.
  3. Write: sections become narration in a teacher's voice, in English or Hindi, several sections per request in
     parallel. Sections that come out much too short are expanded once.
  4. Check it is our own words: every sentence is compared with the source; one that repeats 8 words in a row, or
     shares many 5-word phrases with the source, is rewritten.
  5. Voice and slides: the narration is voiced in short pieces and each section gets a slide (heading and key points)
     drawn by FFmpeg/libass, which renders Hindi correctly. The last card credits the source and says the summary
     was written and voiced with AI.
Ideas, facts and plots are not protected by copyright; the wording is. So the explainer quotes at most a few words and
always names its source.
"""
import json
import math
import re
import subprocess
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from backend.config import FFMPEG_BIN
from backend.video_engine import llm_client

WPM = {"English": 140, "Hindi": 190,      # measured: Sarvam reads 607 Hindi words in about 3 minutes (Hindi words are short)
       "Marathi": 170, "Bengali": 165, "Gujarati": 170, "Punjabi": 180, "Tamil": 140, "Telugu": 150, "Kannada": 140,
       "Malayalam": 130}          # regional rates are estimates until measured
PART_WORDS = 3500
GROUP_WORDS = 11000       # parts read together in one request
MAX_PARTS = 40
SAMPLE_RATE = 24000
PUNCT = ".,;:!?\"'()[]{}—–-।॥“”‘’…*_"
UA = {"User-Agent": "CineCut/9 (explainer)"}

KIND_RULES = {
    "book": "Items are the ideas, arguments, facts or advice in this part.",
    "lecture": "Items are the concepts, definitions, methods, results and worked examples in this part. Skip greetings, admin, jokes and tangents.",
    "story": "Items are the story events in this part: who does what, what changes, what is revealed.",
}
WRITE_RULES = {
    "book": "Explain the ideas so a listener could use them; connect them to everyday life.",
    "lecture": "Teach it: build from the simplest idea, define every term before using it, and walk through one small example.",
    "story": "Retell the story in order with its turning points and what they mean for the characters; do not recreate dialogue.",
}
GENERIC_LANG_RULE = ("Natural spoken {language} in its own script, everyday words. Technical terms that Indians normally "
                     "say in English may stay in English letters.")
LANG_RULES = {
    "English": "Plain, natural spoken English.",
    "Hindi": "Natural spoken Hindi in Devanagari script, everyday words. Technical terms that Indians normally say in English may stay in English letters.",
}

PART_PROMPT = """You are studying part {i} of {n} of the {kind} "{title}"{by}.
Note what this part contributes, entirely in your own words. Never copy a sentence; quote at most five words.
{kind_rules}
Return only JSON:
{{"summary": "two sentences on what this part is about",
  "items": [{{"point": "...", "why": "...", "example": "...", "weight": 2}}],
  "people": ["names in English letters"]}}
At most {n_items} items, in the order they appear. "point": at most 25 words. "why": at most 20 words, why it matters to
the whole work. "example": for ideas, a fresh everyday example of your own (not the author's), at most 20 words; empty for
story events. "weight": 3 = central to the whole work, 2 = important, 1 = detail.

PART {i}:
{text}"""

PARTS_PROMPT = """You are studying parts {a} to {b} of {n} of the {kind} "{title}"{by}.
For each part, note what it contributes, entirely in your own words. Never copy a sentence; quote at most five words.
{kind_rules}
Return only JSON:
{{"parts": [{{"part": {a}, "summary": "two sentences on what this part is about",
  "items": [{{"point": "...", "why": "...", "example": "...", "weight": 2}}], "people": ["names in English letters"]}}]}}
One entry for each part from {a} to {b}. At most {n_items} items per part, in the order they appear. "point": at most 25 words.
"why": at most 20 words, why it matters to the whole work. "example": for ideas, a fresh everyday example of your own (not the
author's), at most 20 words; empty for story events. "weight": 3 = central to the whole work, 2 = important, 1 = detail.

{texts}"""

OUTLINE_PROMPT = """You have notes on every part of the {kind} "{title}"{by}. Plan a {minutes}-minute spoken explainer that
retells its essence in new words: what it is about, its main ideas (or main story turns) in a clear order, and why they matter.
Use exactly {n_sections} sections, covering the whole work from beginning to end, not just the start. Give more room to weight-3 notes.
Return only JSON:
{{"title": "a fresh title for our explainer",
  "thesis": "one sentence: the essence of the whole work",
  "hook": "one or two sentences that open with a question or a vivid moment and make a listener curious",
  "sections": [{{"heading": "at most 6 words", "key_points": ["3 slide bullets, at most 8 words each"], "parts": [part numbers], "goal": "what the listener should understand"}}],
  "takeaways": ["3 to 5 one-line takeaways"],
  "glossary": [{{"term": "...", "meaning": "at most 15 words"}}],
  "quiz": [{{"q": "...", "a": "..."}}]}}
Quiz: 4 questions. Glossary: up to 6 terms (none for a story). Write the heading, key points, takeaways, glossary and quiz in {language}.

NOTES:
{notes}"""

SCRIPT_PROMPT = """Write narration for part of a spoken explainer called "{etitle}", our own-words retelling of the {kind} "{title}"{by}.
The essence: {thesis}
Write in {language}. {lang_rules}
Voice: a warm teacher talking to one curious listener. Explain directly and simply with your own analogies and examples,
short sentences, no lists, no headings read aloud, no "in this section". Never copy the source's sentences; quote at most five words.
{write_rules}
Write each of these sections at about {words} words (this length matters: the narration must fill the time):
{sections}
Return only JSON: {{"sections": [{{"heading": "...", "narration": "..."}}]}} with the sections in the same order."""

EXPAND_PROMPT = """Each narration below is too short for its time slot. Expand each to about the number of words shown by adding
explanation, a concrete example or a consequence, in the same language and voice. Keep what is there. Do not copy the source.
Return only JSON: {{"sections": ["expanded narration", ...]}} in the same order.
{items}"""

REWRITE_PROMPT = """These sentences from our narration repeat the source's wording too closely. Rewrite each in completely new words
with the same meaning, in the same language and at a similar length. Return only JSON: {{"sentences": ["..."]}} in the same order.
{items}"""

INTRO = {"English": "This is our own short explainer of {title}{by}, written in new words.",
         "Hindi": "यह {title}{by} का हमारा अपना छोटा सार है, नए शब्दों में।"}
BY = {"English": " by {author}", "Hindi": " ({author})"}
RECAP_HEADING = {"English": "What to remember", "Hindi": "याद रखने की बातें", "Marathi": "लक्षात ठेवण्यासारखे",
                 "Bengali": "মনে রাখার কথা", "Gujarati": "યાદ રાખવા જેવું", "Punjabi": "ਯਾਦ ਰੱਖਣ ਵਾਲੀਆਂ ਗੱਲਾਂ",
                 "Tamil": "நினைவில் கொள்ள வேண்டியவை", "Telugu": "గుర్తుంచుకోవాల్సినవి", "Kannada": "ನೆನಪಿಡಬೇಕಾದವು",
                 "Malayalam": "ഓർക്കേണ്ട കാര്യങ്ങൾ"}
KICKER = {"English": "OWN-WORDS SUMMARY", "Hindi": "अपने शब्दों में सार"}
END_NOTE = {"English": "Summary written and voiced with AI (CineCut). Not affiliated with the author or publisher.",
            "Hindi": "यह सार AI से लिखा और बोला गया है (CineCut)। लेखक या प्रकाशक से इसका कोई संबंध नहीं है।"}


# ------------------------------------------------------------------ reading sources
def strip_gutenberg(raw: str) -> str:
    start = re.search(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG[^\n]*\n", raw, re.I)
    end = re.search(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG", raw, re.I)
    text = raw[start.end() if start else 0: end.start() if end else len(raw)]
    return text.replace("\r\n", "\n").strip()


def gutenberg_text(text_url: str) -> str:
    r = httpx.get(text_url, headers=UA, timeout=90, follow_redirects=True)
    r.raise_for_status()
    return strip_gutenberg(r.text)


def file_text(path: str) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    if ext in (".srt", ".vtt"):
        from backend.video_engine.subtitle_parser import parse_srt_file
        return transcript_text(parse_srt_file(str(p)))
    return p.read_text(encoding="utf-8", errors="replace")


def transcript_text(subtitles: List[Dict[str, Any]]) -> str:
    """A lecture transcript as paragraphs (a new paragraph after each pause of 2 s or more)."""
    out, cur, last = [], [], None
    for s in subtitles:
        t = re.sub(r"<[^>]+>|\{[^}]*\}|\[[^\]]*\]", " ", s.get("text", "")).strip()
        if not t:
            continue
        if last is not None and s["start"] - last >= 2.0 and cur:
            out.append(" ".join(cur))
            cur = []
        cur.append(t)
        last = s["end"]
    if cur:
        out.append(" ".join(cur))
    return "\n\n".join(out)


def split_parts(text: str) -> List[str]:
    words_total = len(text.split())
    size = max(PART_WORDS, math.ceil(words_total / MAX_PARTS))
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 3:
        paras = [s for s in re.split(r"(?<=[.!?।])\s+", text) if s.strip()]
    parts, cur, n = [], [], 0
    for p in paras:
        w = p.split()
        while len(w) > size:
            if cur:
                parts.append(" ".join(cur))
                cur, n = [], 0
            parts.append(" ".join(w[:size]))
            w = w[size:]
        if n + len(w) > size and cur:
            parts.append(" ".join(cur))
            cur, n = [], 0
        cur.append(" ".join(w))
        n += len(w)
    if cur:
        parts.append(" ".join(cur))
    return [p for p in parts if len(p.split()) > 30] or parts


SCRIPTS = (("Hindi", "ऀ", "ॿ"), ("Bengali", "ঀ", "৿"), ("Punjabi", "਀", "੿"),
           ("Gujarati", "઀", "૿"), ("Tamil", "஀", "௿"), ("Telugu", "ఀ", "౿"),
           ("Kannada", "ಀ", "೿"), ("Malayalam", "ഀ", "ൿ"))


def text_language(text: str) -> str:
    """The language of a text by its script (Devanagari counts as Hindi; Marathi shares it)."""
    sample = text[:20000]
    letters = sum(1 for ch in sample if ch.isalpha()) or 1
    best, share = "English", 0.3
    for name, lo, hi in SCRIPTS:
        n = sum(1 for ch in sample if lo <= ch <= hi) / letters
        if n > share:
            best, share = name, n
    return best


# ------------------------------------------------------------------ originality
def _tokens(text: str) -> List[str]:
    return [t for t in (w.strip(PUNCT).lower() for w in text.split()) if t]


def _grams(toks: List[str], n: int) -> set:
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text or "") if s.strip()]


class SourceIndex:
    def __init__(self, text: str):
        toks = _tokens(text)
        self.g5, self.g8 = _grams(toks, 5), _grams(toks, 8)

    def check(self, sentence: str) -> Tuple[int, int, bool]:
        toks = _tokens(sentence)
        g5 = _grams(toks, 5)
        return len(g5 & self.g5), len(g5), bool(_grams(toks, 8) & self.g8)


def originality(blocks: List[str], index: SourceIndex) -> Tuple[float, List[Tuple[int, int, str]]]:
    """Share of the narration's 5-word phrases not found in the source, and the sentences that copy too much."""
    total = shared = 0
    flagged = []
    for bi, text in enumerate(blocks):
        for si, s in enumerate(sentences(text)):
            sh, n, run8 = index.check(s)
            total += n
            shared += sh
            if run8 or (n >= 4 and sh / n > 0.4):
                flagged.append((bi, si, s))
    return (round(100.0 * (1 - shared / total), 1) if total else 100.0), flagged


# ------------------------------------------------------------------ writing
class _Counter:
    def __init__(self, api_key: Optional[str]):
        self.api_key, self.requests, self.chars_in, self.chars_out = api_key, 0, 0, 0

    def ask(self, prompt: str, temperature: float = 0.4, timeout: float = 240.0) -> Any:
        out = llm_client.generate_json(prompt, api_key=self.api_key, temperature=temperature, timeout=timeout, num_ctx=16384)
        self.requests += 1
        self.chars_in += len(prompt)
        self.chars_out += len(json.dumps(out, ensure_ascii=False))
        return out


def _by(author: str, language: str = "English") -> str:
    return BY.get(language, BY["English"]).format(author=author) if author else ""


def read_parts(parts: List[str], title: str, author: str, kind: str, ask: _Counter,
               progress: Callable[[float, str], None]) -> List[Dict[str, Any]]:
    """Notes for every part. Neighbouring parts are read together (about 11,000 words a request) because free tiers
    limit requests per day more than tokens; a part the batch missed is read on its own."""
    n = len(parts)
    n_items = 6 if kind == "story" else 5
    notes: List[Optional[Dict[str, Any]]] = [None] * n
    groups: List[List[int]] = []
    cur: List[int] = []
    words = 0
    for i, p in enumerate(parts):
        w = len(p.split())
        if cur and words + w > GROUP_WORDS:
            groups.append(cur)
            cur, words = [], 0
        cur.append(i)
        words += w
    if cur:
        groups.append(cur)
    done = [0]

    def one(i: int) -> None:
        prompt = PART_PROMPT.format(i=i + 1, n=n, kind=kind, title=title, by=_by(author), kind_rules=KIND_RULES[kind],
                                    n_items=n_items, text=parts[i])
        for _ in range(2):
            try:
                d = ask.ask(prompt, 0.3)
                if isinstance(d, dict) and d.get("items"):
                    notes[i] = d
                    return
            except Exception:
                continue

    def group(g: List[int]) -> None:
        if len(g) > 1:
            texts = "\n\n".join(f"PART {i + 1}:\n{parts[i]}" for i in g)
            try:
                d = ask.ask(PARTS_PROMPT.format(a=g[0] + 1, b=g[-1] + 1, n=n, kind=kind, title=title, by=_by(author),
                                                kind_rules=KIND_RULES[kind], n_items=n_items, texts=texts), 0.3)
                for x in (d.get("parts") or []) if isinstance(d, dict) else []:
                    try:
                        k = int(x.get("part")) - 1
                    except (TypeError, ValueError, AttributeError):
                        continue
                    if k in g and x.get("items"):
                        notes[k] = x
            except Exception:
                pass
        for i in g:
            if notes[i] is None:
                one(i)
        done[0] += len(g)
        progress(5 + 40 * done[0] / n, f"Read {done[0]} of {n} parts...")

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(group, groups))
    return [x or {"summary": "", "items": [], "people": []} for x in notes]


def _notes_text(notes: List[Dict[str, Any]], limit_chars: int = 60000) -> str:
    lines = []
    for i, d in enumerate(notes, 1):
        items = "; ".join(f"{it.get('point', '')} (why: {it.get('why', '')}) [w{it.get('weight', 1)}]"
                          for it in d.get("items") or [] if isinstance(it, dict))
        lines.append(f"P{i}: {d.get('summary', '')} | {items}")
    text = "\n".join(lines)
    return text if len(text) <= limit_chars else text[:limit_chars]


def _weight(item: Dict[str, Any]) -> int:
    try:
        return int(item.get("weight") or 1)
    except (TypeError, ValueError):
        return 1


def _fallback_outline(notes: List[Dict[str, Any]], title: str, n_sections: int) -> Dict[str, Any]:
    """An outline built from the part notes themselves, used when no AI provider could plan it."""
    items = [(i, it) for i, d in enumerate(notes, 1) for it in (d.get("items") or []) if isinstance(it, dict) and it.get("point")]
    if not items:
        raise RuntimeError("The AI could not read the text. Try again in a minute.")
    per = max(1, math.ceil(len(notes) / n_sections))
    sections = []
    for start in range(0, len(notes), per):
        chunk = sorted([(i, it) for i, it in items if start < i <= start + per], key=lambda x: -_weight(x[1]))
        if chunk:
            sections.append({"heading": " ".join(chunk[0][1]["point"].split()[:5]),
                             "key_points": [" ".join(it["point"].split()[:8]) for _, it in chunk[:3]],
                             "parts": list(range(start + 1, min(len(notes), start + per) + 1)), "goal": chunk[0][1].get("why", "")})
    top = sorted(items, key=lambda x: -_weight(x[1]))
    return {"title": f"{title}: the essence", "thesis": top[0][1].get("why") or top[0][1]["point"], "hook": "",
            "sections": sections[:12], "takeaways": [it["point"] for _, it in top[:4]], "glossary": [], "quiz": [], "fallback": True}


def plan(notes: List[Dict[str, Any]], title: str, author: str, kind: str, minutes: float, language: str, ask: _Counter) -> Dict[str, Any]:
    n_sections = max(4, min(12, int(2 + 0.5 * minutes + 0.5)))
    prompt = OUTLINE_PROMPT.format(kind=kind, title=title, by=_by(author), minutes=int(round(minutes)), n_sections=n_sections,
                                   language=language, notes=_notes_text(notes))
    for _ in range(2):
        try:
            d = ask.ask(prompt, 0.3, timeout=420)
        except Exception:
            continue
        if isinstance(d, dict) and d.get("sections"):
            d["sections"] = [s for s in d["sections"] if isinstance(s, dict) and s.get("heading")][:12]
            if d["sections"]:
                return d
    return _fallback_outline(notes, title, n_sections)


def write_sections(outline: Dict[str, Any], notes: List[Dict[str, Any]], title: str, author: str, kind: str,
                   language: str, words_each: int, ask: _Counter) -> List[str]:
    secs = outline["sections"] + [{"heading": RECAP_HEADING.get(language, RECAP_HEADING["English"]), "key_points": outline.get("takeaways") or [],
                                   "goal": "Recap the takeaways and end with one line on why the work is worth reading or watching.",
                                   "parts": []}]
    texts = [""] * len(secs)

    def block(idx: int, s: Dict[str, Any]) -> str:
        src = []
        for p in s.get("parts") or []:
            try:
                d = notes[int(p) - 1]
            except (ValueError, IndexError, TypeError):
                continue
            src += [f"- {it.get('point', '')} ({it.get('why', '')}){' e.g. ' + it['example'] if it.get('example') else ''}"
                    for it in d.get("items") or [] if isinstance(it, dict)]
        return (f"### Section {idx + 1}: {s['heading']}\nGoal: {s.get('goal', '')}\nKey points: {'; '.join(s.get('key_points') or [])}\n"
                f"Notes:\n" + "\n".join(src[:14]))

    groups = [list(range(i, min(i + 4, len(secs)))) for i in range(0, len(secs), 4)]

    def run(group: List[int]) -> None:
        prompt = SCRIPT_PROMPT.format(etitle=outline.get("title") or title, kind=kind, title=title, by=_by(author),
                                      thesis=outline.get("thesis", ""), language=language, lang_rules=LANG_RULES.get(language, GENERIC_LANG_RULE.format(language=language)),
                                      write_rules=WRITE_RULES[kind], words=words_each,
                                      sections="\n\n".join(block(i, secs[i]) for i in group))
        for attempt in range(2):
            try:
                d = ask.ask(prompt, 0.6, timeout=420)
                got = [x.get("narration", "") if isinstance(x, dict) else str(x) for x in (d.get("sections") or [])]
                if len(got) >= len(group):
                    for i, t in zip(group, got):
                        texts[i] = t.strip()
                    return
            except Exception:
                continue

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run, groups))
    for i, t in enumerate(texts):         # a section no provider could write is told from its part notes
        if not t.strip():
            pts = [it for p in (secs[i].get("parts") or []) if str(p).isdigit() and 0 < int(p) <= len(notes)
                   for it in (notes[int(p) - 1].get("items") or []) if isinstance(it, dict) and it.get("point")]
            pts = sorted(pts, key=lambda it: -_weight(it))[:4] or [{"point": k} for k in secs[i].get("key_points") or []]
            texts[i] = " ".join(f"{it['point'].rstrip('.')}. {it.get('why', '').strip()}".strip() for it in pts)
    short = [i for i, t in enumerate(texts) if len(t.split()) < 0.7 * words_each]
    if short:
        items = "\n\n".join(f"[{k + 1}] about {words_each} words:\n{texts[i]}" for k, i in enumerate(short))
        try:
            d = ask.ask(EXPAND_PROMPT.format(items=items), 0.5)
            for i, t in zip(short, d.get("sections") or []):
                if isinstance(t, str) and len(t.split()) > len(texts[i].split()):
                    texts[i] = t.strip()
        except Exception:
            pass
    return texts


def fix_copying(blocks: List[str], index: SourceIndex, ask: _Counter) -> Tuple[List[str], float, int, int]:
    pct, flagged = originality(blocks, index)
    rewritten = 0
    if flagged:
        items = "\n".join(f"{k + 1}. {s}" for k, (_, _, s) in enumerate(flagged[:40]))
        try:
            d = ask.ask(REWRITE_PROMPT.format(items=items), 0.7)
            new = [x for x in (d.get("sentences") or []) if isinstance(x, str)]
            for (bi, si, old), repl in zip(flagged, new):
                if repl.strip() and index.check(repl)[2] is False:
                    blocks[bi] = blocks[bi].replace(old, repl.strip(), 1)
                    rewritten += 1
        except Exception:
            pass
        pct, flagged = originality(blocks, index)
    return blocks, pct, rewritten, len(flagged)


# ------------------------------------------------------------------ voice
def _tts_pieces(text: str, max_chars: int = 280) -> List[str]:
    out, cur = [], ""
    for s in sentences(text):
        if cur and len(cur) + len(s) + 1 > max_chars:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def _to_wav(src: str, dst: str) -> bool:
    r = subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-i", src, "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", dst],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0 and Path(dst).exists()


def voice(blocks: List[str], voice_key: str, work: Path, progress: Callable[[float, str], None]) -> Tuple[Path, List[Tuple[float, float]], List[Dict[str, Any]]]:
    """Voices every block in short pieces and joins them into one WAV. Returns block times and caption cues."""
    from backend.video_engine.narrator import AVAILABLE_VOICES, synthesize_voiceover, synthesize_many, default_voice_for_language
    tts = work / "tts"
    tts.mkdir(parents=True, exist_ok=True)
    pieces = [(bi, p) for bi, b in enumerate(blocks) for p in _tts_pieces(b)]
    paths = [str(tts / f"{k:03d}.mp3") for k in range(len(pieces))]
    done = synthesize_many([p for _, p in pieces], voice_key, paths)
    info = AVAILABLE_VOICES.get(voice_key, {})
    fallback = info.get("fallback") or default_voice_for_language("Hindi" if info.get("lang_code") == "hi" else "English")
    count = [sum(done)]

    def one(k: int) -> None:
        if done[k] and Path(paths[k]).exists():
            return
        for vk in (voice_key, fallback):
            try:
                synthesize_voiceover(pieces[k][1], vk, paths[k])
            except Exception:
                continue
            if Path(paths[k]).exists() and Path(paths[k]).stat().st_size > 0:
                break
        count[0] += 1
        progress(60 + 25 * count[0] / len(pieces), f"Voicing {count[0]} of {len(pieces)}...")

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(one, range(len(pieces))))
    frames = bytearray()
    gap_piece, gap_block = b"\x00\x00" * int(SAMPLE_RATE * 0.25), b"\x00\x00" * int(SAMPLE_RATE * 0.8)
    block_times: List[List[float]] = [[0.0, 0.0] for _ in blocks]
    cues = []
    last_block = -1
    for k, (bi, text) in enumerate(pieces):
        if bi != last_block:
            if last_block >= 0:
                block_times[last_block][1] = len(frames) / 2 / SAMPLE_RATE
                frames += gap_block
            block_times[bi][0] = len(frames) / 2 / SAMPLE_RATE
            last_block = bi
        wav = paths[k][:-4] + ".wav"
        if not Path(paths[k]).exists() or not _to_wav(paths[k], wav):
            continue
        with wave.open(wav, "rb") as w:
            data = w.readframes(w.getnframes())
        t0 = len(frames) / 2 / SAMPLE_RATE
        frames += data
        cues.append({"start": t0, "end": len(frames) / 2 / SAMPLE_RATE, "text": text})
        frames += gap_piece
    if last_block >= 0:
        block_times[last_block][1] = len(frames) / 2 / SAMPLE_RATE
    frames += gap_block
    out = work / "narration.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))
    return out, [tuple(t) for t in block_times], cues


# ------------------------------------------------------------------ slides
BG = "0x11151c"
C_TEXT, C_MUTED, C_ACCENT = "&H00F4F1EC", "&H009A948C", "&H0047B5FF"


def _esc(t: str) -> str:
    return (t or "").replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def slides_ass(outline: Dict[str, Any], block_times: List[Tuple[float, float]], total: float, source_line: str,
               language: str, end_card: Tuple[float, float]) -> str:
    font = "Nirmala UI"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{font},86,{C_TEXT},{C_TEXT},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1
Style: Kicker,{font},32,{C_ACCENT},{C_ACCENT},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1
Style: Heading,{font},66,{C_TEXT},{C_TEXT},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1
Style: Point,{font},46,&H00DCD6CF,&H00DCD6CF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,190,160,60,1
Style: Small,{font},28,{C_MUTED},{C_MUTED},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,140,160,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []

    def add(start: float, end: float, style: str, text: str, pos: Tuple[int, int], extra: str = "") -> None:
        ev.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{{\\pos({pos[0]},{pos[1]}){extra}}}{text}")

    secs = outline["sections"]
    n = len(secs)
    # title card over the intro
    s0, e0 = block_times[0][0], (block_times[1][0] if len(block_times) > 1 else block_times[0][1])
    add(s0, e0, "Kicker", _esc(KICKER.get(language, KICKER["English"])), (140, 330))
    add(s0, e0, "Title", _esc(outline.get("title", "")), (140, 390), "\\fad(400,0)")
    add(s0, e0, "Small", _esc(source_line), (140, 700), "\\fad(600,0)")
    for i, (bs, be) in enumerate(block_times[1:], 1):
        nxt = block_times[i + 1][0] if i + 1 < len(block_times) else end_card[0]
        if i <= n:
            s = secs[i - 1]
            kicker, heading, points = f"{i} / {n}", s.get("heading", ""), (s.get("key_points") or [])[:4]
        else:
            kicker, heading, points = "", RECAP_HEADING.get(language, RECAP_HEADING["English"]), (outline.get("takeaways") or [])[:5]
        ev.append(f"Dialogue: 0,{_ts(bs)},{_ts(nxt)},Kicker,,0,0,0,,{{\\pos(96,196)\\p1\\c{C_ACCENT}&}}m 0 0 l 10 0 10 96 0 96{{\\p0}}")
        if kicker:
            add(bs, nxt, "Kicker", kicker, (140, 150))
        add(bs, nxt, "Heading", _esc(heading), (140, 196), "\\fad(300,0)")
        step = (be - bs) / (len(points) + 1) if points else 0
        for k, p in enumerate(points):
            add(bs + step * k * 0.8, nxt, "Point", "•  " + _esc(p), (140, 390 + k * 118), "\\fad(350,0)")
        add(bs, nxt, "Small", _esc(source_line), (140, 1000))
    add(end_card[0], end_card[1], "Kicker", "SOURCE", (140, 330))
    add(end_card[0], end_card[1], "Point", _esc(source_line), (140, 400))
    add(end_card[0], end_card[1], "Small", _esc(END_NOTE.get(language, END_NOTE["English"])), (140, 640))
    return head + "\n".join(ev) + "\n"


def render_video(work: Path, narration: Path, ass_text: str, total: float, out_path: Path) -> None:
    (work / "slides.ass").write_text(ass_text, encoding="utf-8")
    cmd = [FFMPEG_BIN, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={BG}:s=1920x1080:r=24:d={total:.2f}",
           "-i", narration.name, "-vf", "ass=slides.ass", "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "22", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out_path.resolve())]
    r = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out_path.exists():
        raise RuntimeError("Slide video failed: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:200])


def _srt(cues: List[Dict[str, Any]]) -> str:
    def t(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{t(c['start'])} --> {t(c['end'])}\n{c['text']}\n" for i, c in enumerate(cues, 1))


def _script_md(outline: Dict[str, Any], blocks: List[str], meta: Dict[str, Any], language: str) -> str:
    heads = ["Opening"] + [s.get("heading", "") for s in outline["sections"]] + [RECAP_HEADING.get(language, RECAP_HEADING["English"])]
    md = [f"# {outline.get('title', '')}", "", f"*Our own-words summary of {meta['title']}{_by(meta.get('author', ''))}.*", "",
          f"Source: {meta.get('attribution') or meta.get('origin') or 'provided by the user'}", "",
          f"**Essence:** {outline.get('thesis', '')}", ""]
    for h, b in zip(heads, blocks):
        md += [f"## {h}", "", b, ""]
    if outline.get("glossary"):
        md += ["## Glossary", ""] + [f"- **{g.get('term', '')}**: {g.get('meaning', '')}" for g in outline["glossary"] if isinstance(g, dict)] + [""]
    if outline.get("quiz"):
        md += ["## Check yourself", ""] + [f"{i}. {q.get('q', '')}  \n   *{q.get('a', '')}*" for i, q in enumerate(outline["quiz"], 1) if isinstance(q, dict)] + [""]
    md += ["---", END_NOTE.get(language, END_NOTE["English"]), f"Originality: {meta.get('originality_pct')}% of 5-word phrases are new (not in the source)."]
    return "\n".join(md)


# ------------------------------------------------------------------ main
def make_explainer(source: Dict[str, Any], kind: str, minutes: float, language: str, voice_key: Optional[str], work: Path,
                   out_dir: Path, render: bool = True, api_key: Optional[str] = None,
                   progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """source: {title, author, text, attribution, origin}. Writes explainer.mp4, script.md, notes.json and explainer.srt
    to out_dir (work files go to work) and returns the plan, the narration and measurements."""
    progress = progress or (lambda p, m: None)
    kind = kind if kind in KIND_RULES else "book"
    language = language if language in WPM else "English"
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    text, title, author = source["text"], source.get("title") or "Untitled", source.get("author") or ""
    ask = _Counter(api_key)
    t0 = time.time()
    parts = split_parts(text)
    if not parts:
        raise ValueError("The text is empty.")
    progress(5, f"Reading {len(parts)} part(s)...")
    notes = read_parts(parts, title, author, kind, ask, progress)
    t_read = time.time()
    progress(46, "Planning the explainer from the whole work...")
    outline = plan(notes, title, author, kind, minutes, language, ask)
    words_total = int(minutes * WPM[language])
    intro = (outline.get("hook", "").strip() + " " + INTRO.get(language, "").format(title=title, by=_by(author, language))).strip()
    words_each = max(60, int((words_total - len(intro.split()) - 60) / (len(outline["sections"]) + 0.6)))
    progress(50, "Writing the narration in our own words...")
    body = write_sections(outline, notes, title, author, kind, language, words_each, ask)
    blocks = [intro] + body
    t_write = time.time()
    progress(56, "Checking that the wording is our own...")
    same_lang = text_language(text) == language
    index = SourceIndex(text) if same_lang else None
    if index:
        blocks, orig_pct, rewritten, still = fix_copying(blocks, index, ask)
    else:
        orig_pct, rewritten, still = None, 0, 0
    words = sum(len(b.split()) for b in blocks)
    covered = sorted({int(p) for s in outline["sections"] for p in (s.get("parts") or []) if str(p).isdigit()})
    meta = {"title": title, "author": author, "attribution": source.get("attribution"), "origin": source.get("origin"),
            "originality_pct": orig_pct}
    source_line = f"{title}{_by(author)}" + (f" · {source['license_label']}" if source.get("license_label") else "")
    result: Dict[str, Any] = {
        "title": outline.get("title"), "thesis": outline.get("thesis"), "hook": outline.get("hook"),
        "sections": [{"heading": s.get("heading"), "key_points": s.get("key_points"), "narration": b}
                     for s, b in zip(outline["sections"] + [{"heading": RECAP_HEADING.get(language, RECAP_HEADING["English"]), "key_points": outline.get("takeaways")}], body)],
        "intro": intro, "takeaways": outline.get("takeaways"), "glossary": outline.get("glossary"), "quiz": outline.get("quiz"),
        "originality_pct": orig_pct, "originality_note": None if same_lang else "Source and narration are in different languages, so phrases were not compared.",
        "rewritten_sentences": rewritten, "still_flagged": still, "words": words, "target_words": words_total,
        "parts": len(parts), "parts_covered": len(covered), "source_words": len(text.split()), "language": language, "kind": kind,
        "stats": {"requests": ask.requests, "prompt_chars": ask.chars_in, "reply_chars": ask.chars_out,
                  "read_sec": round(t_read - t0, 1), "write_sec": round(t_write - t_read, 1)}}
    (out_dir / "script.md").write_text(_script_md(outline, blocks, meta, language), encoding="utf-8")
    (out_dir / "notes.json").write_text(json.dumps({"outline": outline, "part_notes": notes}, ensure_ascii=False, indent=1), encoding="utf-8")
    result["files"] = {"script": str(out_dir / "script.md"), "notes": str(out_dir / "notes.json")}
    if render:
        from backend.video_engine.narrator import default_voice_for_language
        vk = voice_key or default_voice_for_language(language)
        t_v = time.time()
        progress(60, "Voicing the narration...")
        narration, block_times, cues = voice(blocks, vk, work, progress)
        speech_end = block_times[-1][1] + 0.8
        end_card = (speech_end, speech_end + 5.0)
        with wave.open(str(narration), "rb") as w:
            data = w.readframes(w.getnframes())
        with wave.open(str(narration), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(data + b"\x00\x00" * int(SAMPLE_RATE * (end_card[1] - len(data) / 2 / SAMPLE_RATE + 0.2)))
        total = end_card[1]
        progress(88, "Drawing the slides and rendering...")
        ass = slides_ass(outline, block_times, total, source_line, language, end_card)
        video = out_dir / "explainer.mp4"
        render_video(work, narration, ass, total, video)
        (out_dir / "explainer.srt").write_text(_srt(cues), encoding="utf-8")
        result["files"].update({"video": str(video), "srt": str(out_dir / "explainer.srt")})
        result.update({"minutes": round(total / 60, 2), "voice": vk})
        result["stats"]["voice_render_sec"] = round(time.time() - t_v, 1)
    result["stats"]["total_sec"] = round(time.time() - t0, 1)
    progress(100, "Explainer ready.")
    return result
