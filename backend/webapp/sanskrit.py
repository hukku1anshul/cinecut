"""
The original Sanskrit of a Rigveda hymn, for the opening of a Vedas video: its first mantra in Devanagari with the hymn's
seer (ऋषि), deity (देवता) and metre (छन्द), from Sanskrit Wikisource ("ऋग्वेदः सूक्तं 1.1" style pages, digits in
Devanagari), and a scholarly transliteration (IAST) for English viewers. The Vedic text is ancient and in the public domain;
only the mantra itself and the index facts are used, never the page's commentary. Cached in output/youtube/_scripts.
"""
import json
import re
import unicodedata
from typing import Any, Dict, Optional

import httpx

from backend.video_engine import rights
from backend.webapp.storyteller import CACHE

API = "https://sa.wikisource.org/w/api.php"
DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def mantra_card(book: int, hymn: int, verse: int = 1) -> Optional[Dict[str, Any]]:
    """{"lines": [the mantra's lines], "rishi", "devata", "chhanda", "source": page title, "ref": "1.1"} or None. `verse`
    picks a famous verse other than the first (the Gayatri is 3.62.10); its ref then names it ("3.62.10")."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (f"sanskrit_rv_{book}_{hymn}.json" if verse == 1 else f"sanskrit_rv_{book}_{hymn}_v{verse}.json")
    if path.exists():
        card = json.loads(path.read_text(encoding="utf-8"))
    else:
        card = _fetch(book, hymn, verse)
        path.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    if not card.get("lines"):
        return None
    if card.get("devata"):                           # "१-४१ विश्वे देवाः": the deity of the first verses
        card["devata"] = re.sub(r"^[\d०-९\s,\-–]+", "", card["devata"]).strip()
    return dict(card, ref=f"{book}.{hymn}" if verse == 1 else f"{book}.{hymn}.{verse}")


def _fetch(book: int, hymn: int, verse: int = 1) -> Dict[str, Any]:
    title = f"ऋग्वेदः सूक्तं {book}.{hymn}".translate(DIGITS)
    try:
        r = httpx.get(API, headers=rights.UA, timeout=40,
                      params={"action": "parse", "page": title, "prop": "wikitext", "format": "json", "redirects": 1}).json()
        text = r["parse"]["wikitext"]["*"]
    except (httpx.HTTPError, ValueError, KeyError):
        return {}
    card: Dict[str, Any] = {"source": title}
    m = re.search(r"\|\s*author\s*=\s*([^\n|]+)", text)
    if m:
        card["rishi"] = re.sub(r"[।\s]+$", "", re.sub(r"\[\[|\]\]", "", m.group(1))).strip()
    m = re.search(r"\|\s*notes\s*=\s*([^\n]+)", text)
    if m:
        notes = re.sub(r"\[\[|\]\]", "", m.group(1))
        d = re.search(r"दे\.\s*([^।,]+)", notes)
        if d:
            card["devata"] = d.group(1).strip()
        c = re.search(r"।\s*([^।,\d०-९]+?)(?:[,।]|\s*[०-९\d]|$)", notes)
        if c:
            card["chhanda"] = c.group(1).strip()
    poems = re.findall(r"<poem>(.*?)</poem>", text, re.S)      # a page may split the hymn into several blocks (3.62 does)
    if poems:
        body = re.sub(r"<[^>]+>", "", "\n".join(poems))
        mark = str(verse).translate(DIGITS)
        prev = str(verse - 1).translate(DIGITS)
        lines, started = [], verse == 1
        for ln in (x.strip() for x in body.splitlines()):
            if not ln:
                continue
            if not started:                              # skip to the line after the previous verse's mark
                if re.search(rf"॥\s*({prev}|{verse - 1})\s*॥", ln):
                    started = True
                continue
            lines.append(ln)
            if re.search(rf"॥\s*({mark}|{verse})\s*॥", ln):   # the lines up to this verse's mark, ॥१०॥
                break
        if 1 <= len(lines) <= 4 and re.search(rf"॥\s*({mark}|{verse})\s*॥", lines[-1]):
            card["lines"] = lines
    return card if card.get("lines") else {}


# ------------------------------------------------------------------ IAST
_V = {"अ": "a", "आ": "ā", "इ": "i", "ई": "ī", "उ": "u", "ऊ": "ū", "ऋ": "ṛ", "ॠ": "ṝ", "ऌ": "ḷ", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au"}
_M = {"ा": "ā", "ि": "i", "ी": "ī", "ु": "u", "ू": "ū", "ृ": "ṛ", "ॄ": "ṝ", "ॢ": "ḷ", "े": "e", "ै": "ai", "ो": "o", "ौ": "au"}
_C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ṅ", "च": "c", "छ": "ch", "ज": "j", "झ": "jh", "ञ": "ñ", "ट": "ṭ", "ठ": "ṭh",
      "ड": "ḍ", "ढ": "ḍh", "ण": "ṇ", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
      "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "ḷ", "व": "v", "श": "ś", "ष": "ṣ", "स": "s", "ह": "h"}
_S = {"ं": "ṃ", "ः": "ḥ", "ँ": "m̐", "ऽ": "'", "ॐ": "oṃ", "।": "|", "॥": "||"}


def iast(text: str) -> str:
    """Devanagari to IAST ("अग्निमीळे पुरोहितं" -> "agnimīḷe purohitaṃ"); verse numbers and Vedic accent marks dropped."""
    s = unicodedata.normalize("NFC", text)
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if nxt == "़":                                   # a nukta (not used in Sanskrit): read the plain letter
            s = s[:i + 1] + s[i + 2:]
            nxt = s[i + 1] if i + 1 < len(s) else ""
        if ch in _C:
            out.append(_C[ch])
            if nxt == "्":
                i += 2
            elif nxt in _M:
                out.append(_M[nxt])
                i += 2
            else:
                out.append("a")                          # the inherent vowel
                i += 1
            continue
        if ch in _V:
            out.append(_V[ch])
        elif ch in _S:
            out.append(_S[ch])
        elif "॑" <= ch <= "॔" or "᳐" <= ch <= "᳿" or ch in "०१२३४५६७८९":
            pass
        else:
            out.append(ch)
        i += 1
    return re.sub(r"\s+", " ", re.sub(r"\|\|\s*\|\|", "||", "".join(out))).strip()
