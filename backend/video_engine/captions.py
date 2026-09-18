"""
Animated captions for 9:16 reels, written as ASS subtitles that FFmpeg (libass) burns into the video:
  - karaoke: each word lights up (white to yellow) the moment it is spoken
  - pop:     the word being spoken is highlighted and slightly larger
  - plain:   short caption chunks without animation
Word times come from the transcript when it has them (Groq Whisper word timestamps); otherwise each caption
line's time is shared across its words by length. Nirmala UI renders Hindi (Devanagari) and English.
"""
from typing import Any, Dict, List, Optional

FONT = "Nirmala UI"
YELLOW = "&H0000D4FF"        # style colour, ASS order AABBGGRR, for #FFD400
YELLOW_TAG = "&H00D4FF&"      # inline override colour
WHITE = "&H00FFFFFF"
MAX_WORDS = 5
MAX_CHARS = 30
STYLES = ("karaoke", "pop", "plain")


def _ts(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(word: str) -> str:
    return word.replace("\\", "/").replace("{", "(").replace("}", ")")


def _split_words(text: str, start: float, end: float) -> List[Dict[str, Any]]:
    """Shares a caption line's time across its words, longer words getting more time."""
    words = text.split()
    if not words:
        return []
    weights = [len(w) + 1 for w in words]
    total = float(sum(weights))
    dur = max(0.2, end - start)
    out, t = [], start
    for w, wt in zip(words, weights):
        d = dur * wt / total
        out.append({"word": w, "start": t, "end": t + d})
        t += d
    return out


def word_track(cues: List[Dict[str, Any]], words: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """cues: [{start, end, text}] relative to the reel. words: optional [{word, start, end}] relative (from ASR)."""
    track: List[Dict[str, Any]] = []
    for c in cues:
        inside = [w for w in (words or []) if c["start"] - 0.25 <= w["start"] <= c["end"] + 0.25 and str(w.get("word", "")).strip()]
        if inside:
            track += [{"word": str(w["word"]).strip(), "start": float(w["start"]), "end": float(w["end"])} for w in inside]
        else:
            track += _split_words(c["text"], c["start"], c["end"])
    track.sort(key=lambda w: w["start"])
    return track


def _chunks(track: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    chunks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    for w in track:
        too_long = sum(len(x["word"]) + 1 for x in cur) + len(w["word"]) > MAX_CHARS
        if cur and (len(cur) >= MAX_WORDS or too_long or w["start"] - cur[-1]["end"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["word"].endswith((".", "?", "!", "।")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(cues: List[Dict[str, Any]], words: Optional[List[Dict[str, Any]]] = None, style: str = "karaoke",
              width: int = 1080, height: int = 1920) -> str:
    style = style if style in STYLES else "karaoke"
    track = word_track(cues, words)
    size = int(height * 0.036)
    margin_v = int(height * 0.16)
    primary = YELLOW if style == "karaoke" else WHITE
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        f"Style: Cap,{FONT},{size},{primary},{WHITE},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines: List[str] = []
    chunks = _chunks(track)
    for n, ch in enumerate(chunks):
        c0 = ch[0]["start"]
        nxt = chunks[n + 1][0]["start"] if n + 1 < len(chunks) else ch[-1]["end"] + 0.4
        c1 = max(ch[-1]["end"], min(nxt, ch[-1]["end"] + 0.4))   # hold the line briefly, never over the next one
        if style == "karaoke":
            parts = []
            for j, w in enumerate(ch):
                upto = ch[j + 1]["start"] if j + 1 < len(ch) else w["end"]
                cs = max(1, int(round((upto - w["start"]) * 100)))
                parts.append("{\\k%d}%s" % (cs, _esc(w["word"])))
            lines.append(f"Dialogue: 0,{_ts(c0)},{_ts(c1)},Cap,,0,0,0,,{' '.join(parts)}")
        elif style == "pop":
            for j, w in enumerate(ch):
                w1 = ch[j + 1]["start"] if j + 1 < len(ch) else c1
                txt = " ".join(("{\\c%s\\fscx112\\fscy112}%s{\\r}" % (YELLOW_TAG, _esc(x["word"]))) if k == j else _esc(x["word"])
                               for k, x in enumerate(ch))
                lines.append(f"Dialogue: 0,{_ts(w['start'])},{_ts(w1)},Cap,,0,0,0,,{txt}")
        else:
            lines.append(f"Dialogue: 0,{_ts(c0)},{_ts(c1)},Cap,,0,0,0,,{' '.join(_esc(w['word']) for w in ch)}")
    return header + "\n".join(lines) + "\n"
