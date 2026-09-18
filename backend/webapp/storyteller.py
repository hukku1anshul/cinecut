"""
Documentary scripts for the YouTube videos: a finished summary retold the way a good narrator talks, not the way a
summary reads.

The library's summaries are built for reading and listening in the app: headings, bullet points, a recap. On YouTube that
shape (and the stock phrases language models reach for) is what makes a video feel machine-made. So each video gets its
own script, written from the summary and, where the source is a public-domain text we can fetch, from the text itself:

- a cold open on a concrete moment, image or question from the work; no greeting, no "in this video";
- chapters that hand over to each other like one story, with specific names, places, numbers and short real quotations
  from the public-domain text (translated into simple Hindi for Hindi videos);
- short and long sentences mixed, contractions in English, the odd honest aside, scholars' disagreements said plainly;
- no lists, no recap, no "key takeaways"; it ends on one thought or open question;
- a list of tell-tale phrases is checked after writing, and the sentences that use them are rewritten.

Scripts are cached in output/youtube/_scripts, one file per title and language, so a remake does not ask again.
"""
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import OUTPUT_DIR
from backend.video_engine import llm_client

CACHE = OUTPUT_DIR / "youtube" / "_scripts"
BANNED = {
    "English": [r"\bdelv\w*", r"\btapestr\w*", r"\btestament\b", r"\bessence\b", r"\bjourney\b", r"\brealms?\b", r"\bembark\w*",
                r"\bunlock\w*", r"\bin conclusion\b", r"\bin summary\b", r"\bto sum up\b", r"\bkey takeaways?\b", r"\bpoints to remember\b",
                r"\blet'?s dive\b", r"\bdive (?:in|into|deep)\b", r"\bfascinating\b", r"\bprofound\w*", r"\btimeless\b",
                r"\bit(?:'s| is) (?:important|worth) (?:to note|noting)\b", r"\bmoreover\b", r"\bfurthermore\b", r"\bin today'?s world\b",
                r"\bresonat\w*", r"\bnavigat\w*", r"\bmultifaceted\b", r"\bintricat\w*", r"\bserves? as a (?:reminder|testament)\b",
                r"\bstands? as\b", r"\bwelcome (?:back )?to\b", r"\bin this video\b", r"\bbuckle up\b", r"\bgame[- ]changer\b", r"\bunveil\w*",
                r"\bever[- ]evolving\b", r"\bat its core\b", r"\bnot (?:just|only|merely) [^.;]{1,60}, but\b", r"\bdelicate balance\b",
                r"\bbeacon\b", r"\bpivotal\b", r"\bunderscore\w*", r"\bin the grand scheme\b", r"\bthe power of\b", r"\bhuman condition\b",
                # the "not X; it's Y" contrast, however it is punctuated
                r"\bnot (?:just|only|merely|simply) [^.;]{1,60}[;,] (?:but|it['’]s|it is|he is|he['’]s|she is|they are)\b",
                r"\b(?:isn['’]t|is not|aren['’]t|wasn['’]t|was not) (?:about |just |only |merely )?[^.;]{1,50}; (?:it|he|she|they|this)(?:['’]s| is| are| was)\b",
                r"\bis not an? [^.;]{1,40}\. (?:It|He|She|This) is\b",
                r"\b(?:isn['’]t|is not|aren['’]t|are not|wasn['’]t|was not) (?:about )?[^.;,]{1,60}, but\b",
                r"\bmore than (?:just|merely|simply) (?:a |an |the |someone |something )?[^.;,]{1,40}[.;] (?:It|He|She|They|This)(?:['’]s| is| are)\b",
                # modern comparisons in videos about ancient texts ("Forget the internet", "like electricity runs a city")
                r"\binternet\b", r"\bonline\b", r"\belectricit\w*", r"\boxygen\b", r"\batoms?\b", r"\bcomputers?\b", r"\bsmart ?phones?\b",
                r"\bsoftware\b", r"\balgorithms?\b", r"\bwi-?fi\b", r"\borchestra\w*", r"\bbedrock\b", r"\bblueprints?\b", r"\bdownload\w*",
                r"\bupgrade\w*", r"\bdigital\b", r"\blighthouses?\b", r"\bmachines?\b", r"\bengines?\b", r"\bfactor(?:y|ies)\b",
                r"\bbatter(?:y|ies)\b", r"\bsatellites?\b", r"\bradio\b", r"\btelevision\b", r"\bemails?\b", r"\bGPS\b",
                # empty lines a viewer hears as machine-written
                r"\bimagine\b", r"\breminders?\b", r"\bwithin us\b", r"\blife['’]s (?:challenges|journey|ups)\b", r"\bfather figure\b",
                r"\bbridg\w* the gap\b", r"\bsymbol of\b", r"\bguiding us\b", r"\bdivine destination\b", r"\bnestled\b", r"\bbustling\b",
                r"\bawe[- ]inspiring\b", r"\bshrouded in mystery\b", r"\bsands of time\b", r"\bcaptivat\w*", r"\bcatalyst\w*",
                r"\btangible\b", r"\b(?:does|do|did)n['’]t (?:just|simply|merely)\b", r"\b(?:starts?|begins?) not with\b",
                r"\bin balance\b", r"\b(?:isn['’]t|is not|wasn['’]t) (?:about|really about)\b", r"\byou aren['’]t alone\b"],
    "Hindi": [r"आइए जानते हैं", r"आइए गहराई", r"निष्कर्ष", r"महत्वपूर्ण बात यह है", r"इस वीडियो में", r"स्वागत है", r"याद रखने की बातें",
              r"सारांश", r"(?<![ऀ-ॿ])गहन(?:ता)?(?![ऀ-ॿ])", r"अद्भुत यात्रा", r"यात्रा पर",
              r"(?:केवल|सिर्फ़|सिर्फ|मात्र|कोई|एक) [^।,]{1,50}नहीं(?: है| हैं| था| थे| थी)?,? (?:बल्कि|वरन्|अपितु)",      # "not merely X, but Y"
              r"कालजयी", r"प्रेरणादायक", r"अनमोल सीख"] + [rf"(?<![ऀ-ॿ]){w}(?![ऀ-ॿ])" for w in (
                  # English words in Devanagari, and Urdu or Persian words that have a plain Hindi equivalent (the viewer asked for pure Hindi)
                  "कॉस्मिक", "ब्लूप्रिंट", "रीसाइक्लिंग", "बैलेंस", "सोर्स", "सिस्टम", "प्रोसेस", "टाइम", "लाइफ", "वीडियो", "कनेक्शन", "एनर्जी",
                  "ज़्यादा", "ज्यादा", "आख़िरी", "आखिरी", "आख़िर", "आखिर", "मुमकिन", "कायनात", "ख़ुद", "खुद", "जवाब", "सवाल", "सवालों",
                  "हिस्सा", "हिस्से", "हिस्सों", "दुनिया", "ज़िंदगी", "जिंदगी", "इंसान", "इंसानों", "मतलब", "वजह", "कोशिश", "आसान",
                  "मुश्किल", "हमेशा", "ज़रूरी", "जरूरी", "लेकिन", "ख़ास", "खास", "इस्तेमाल", "बेशक", "असल", "तरीका", "तरीके", "सफ़र", "नज़रिया", "नजरिया", "नज़रिए", "नजरिए", "आसमान", "म्यूज़ियम", "म्यूजियम", "गैस", "इंटरनेट", "ऑक्सीजन", "कंप्यूटर", "सॉफ्टवेयर", "डिजिटल", "ऑर्केस्ट्रा", "ब्लूप्रिंट",
                  "अक्सर", "शायद", "बेहद", "ज़रिए", "जरिए", "ज़रिये", "काफ़ी", "काफी", "तरह",
                  "वो")],       # (ये stays: it is the correct plural, "these")
}
LANGUAGE_RULES = {
    "English": ("Write in natural spoken English{accent}. Use contractions (it's, that's, don't) the way people speak. "
                "Quotations from the source: quote them exactly, and say who translated them when it is a translation."),
    "Hindi": ("Write in pure, standard Hindi (शुद्ध मानक हिंदी) in Devanagari, the way Doordarshan and Akashvani narrators speak: correct, "
              "dignified, clear and easy to follow. Use no English words at all (not 'cosmic', 'blueprint', 'recycling', 'balance', 'source', "
              "'system', 'video'), and prefer Hindi words over Urdu or Persian ones: अधिक not ज़्यादा, अंतिम not आख़िरी, संभव not मुमकिन, "
              "ब्रह्मांड not कायनात, स्वयं not ख़ुद, उत्तर not जवाब, प्रश्न not सवाल, भाग not हिस्सा, संसार not दुनिया, जीवन not ज़िंदगी, "
              "मनुष्य not इंसान, अर्थ not मतलब, कारण not वजह, प्रयास not कोशिश, सरल not आसान, परंतु not लेकिन. Write यह/वह, not ये/वो. "
              "Avoid rare, heavy Sanskrit words when a common Hindi word exists. "
              "Quotations from an English translation: put them into pure Hindi and introduce them naturally (for example: कवि कहता है, ...). "
              "Quotations from a Hindi original: quote them exactly."),
}
ACCENT = {"gb": " (British English spelling and idiom)", "us": " (American English spelling and idiom)"}
PROMPT = """You are an experienced documentary writer. Write the narration for a {minutes}-minute YouTube video about {what}.
The video is spoken by one narrator over paintings. It must sound like a real person who knows this subject well, talking to
one viewer; it must not sound like a summary or like a machine.

What the video should cover, in order (our earlier notes):
{notes}
{source_block}
How it must sound:
- Open cold with a concrete moment, image, line or question from the work itself. No greeting, no "in this video", no title read aloud.
- Tell it as one story: each chapter hands over to the next naturally. Specific over general: names, places, numbers, what exactly
  happens or is said. No vague praise of the work.
- Mix short and long sentences. The occasional honest aside is welcome ("Nobody knows exactly why." "This is the strange part.").
- {quote_rule}
- No lists, no "firstly", no recap at the end. End on one striking thought or an open question, in two or three sentences.
- Where scholars or traditions read something differently, say so plainly in a sentence.
- Never use these words or phrases: {banned}. Never write "not just X, but Y".
{language_rule}

Chapter titles: 2 to 6 words, specific and curious (like "A giant with a thousand heads"), never "The nature of...", never
"Introduction" or "Conclusion". Video title: under 70 characters, specific and intriguing like a good documentary title; no
"Essence", "Explained", "Summary" and no "X: Y" subtitle formula.

Write {n} chapters; chapter 1 is the cold open (about {open_words} words), the others about {words} words each.
Return only JSON:
{{"title": "...", "chapters": [{{"title": "...", "narration": "...", "quote": "one short quotation (under 20 words) exactly as it is spoken in this chapter's narration, or empty"}}]}}
Titles and narration in {language}."""
# added to the prompt when it is sent (kept out of PROMPT so that scripts already approved keep their cache key)
ACCURACY = """
Accuracy: add nothing that is not in the notes or the text above; no invented scenes, numbers or claims. Be careful with
history: Vedic worship took place at fire altars in the open air, not in temples, and used no images of the gods; say
"the hymn says" or "the poet asks" rather than inventing what people felt or did. Use images from the work's own world (fire,
rivers, dawn, cattle, the sacrifice, the forest, the village); never modern comparisons (the internet, electricity, oxygen,
atoms, computers, orchestras, software)."""
# added to the prompt for a hymn of the Rigveda (kept out of PROMPT so that approved scripts keep their cache key)
HYMN_RULE = """
This is one hymn of the Rigveda. Walk the viewer through it in order, a few verses per chapter, so the whole hymn is heard:
say what each verse asks or praises, with its own images, and explain its unfamiliar words plainly (for example Hotar: the
priest who calls the gods; Law eternal: Rita, the true order of things). Words inside quotation marks must be Griffith's exact
words from the text above: a whole line or half a line, never a paraphrase, never words from two verses joined together.
Everything else is said in your own words."""
QUOTE_FIX_PROMPT = """Each sentence below puts words in quotation marks that are not exactly in the source text. Rewrite each sentence
so that the quotation is replaced by the exact words of the line it comes from (copied from the source, a whole line or half a
line, from one verse only), or, if no line fits, remove the quotation marks and say it in your own words. Keep the rest of the
sentence. Return only JSON: {{"sentences": ["...", ...]}} in the same order.
Source:
<<<
{source}
>>>
Sentences:
{items}"""
QUOTED = re.compile(r"[“\"]([^”\"]{8,}?)[”\"]|(?<![\w’'])['‘]([^'’]{8,}?)['’](?!\w)")


def _flat(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def misquotes(text: str, source: str) -> List[str]:
    """Quotations (three words or more) that are not word for word in the source (punctuation and case aside)."""
    src = " " + _flat(source) + " "
    return [q for q in ((m.group(1) or m.group(2) or "").strip() for m in QUOTED.finditer(text or ""))
            if len(q.split()) >= 3 and f" {_flat(q)} " not in src]


def _fix_quotes(d: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Sentences with a wrong quotation rewritten with the exact line (or without quotation marks); a rewrite is kept only if
    it has no wrong quotation left."""
    todo = []
    for ci, c in enumerate(d["chapters"]):
        for sent in _sentences(c["narration"]):
            if misquotes(sent, source):
                todo.append((ci, sent))
    if not todo:
        return d
    items = "\n".join(f"{k + 1}. {sent}" for k, (_, sent) in enumerate(todo))
    try:
        out = llm_client.generate_json(QUOTE_FIX_PROMPT.format(source=source[:6000], items=items), temperature=0.2, timeout=300,
                                       num_ctx=16384)
        new = [x for x in (out or {}).get("sentences") or [] if isinstance(x, str)]
    except Exception:
        new = []
    for (ci, old), fixed in zip(todo, new):
        if fixed.strip() and not misquotes(fixed, source):
            d["chapters"][ci]["narration"] = d["chapters"][ci]["narration"].replace(old, fixed.strip(), 1)
    return d


def mark_verses(d: Dict[str, Any], source: str) -> Dict[str, Any]:
    """A sentence that is the source's exact words (five words or more) is put in quotation marks, so it is read by the elder
    voice and shown as a quotation."""
    src = " " + _flat(source) + " "
    for c in d["chapters"]:
        nar = c["narration"]
        for sent in _sentences(nar):
            body = sent.strip()
            if QUOTED.search(body) or len(body.split()) < 5 or f" {_flat(body)} " not in src:
                continue
            nar = nar.replace(body, f"“{body}”", 1)
        c["narration"] = nar
    return d


def _problems(chapters: List[Dict[str, Any]], source: str, language: str) -> int:
    text = " ".join(c.get("narration") or "" for c in chapters)
    return 3 * len(misquotes(text, source)) * (language == "English") + len(banned_hits(text, language))


FIX_PROMPT = """Rewrite each sentence below so it keeps its meaning but avoids the word or phrase marked in brackets, in plain spoken {language}.
{extra}Keep any quotation as it is. Return only JSON: {{"sentences": ["...", ...]}} in the same order.
{items}"""
FIX_EXTRA = {"Hindi": ("Write pure, standard Hindi: replace every English word and every Urdu or Persian word in the sentence (not only the marked "
                       "one) with a plain Hindi word (संसार, मनुष्य, जीवन, प्रयास, आकाश, दृष्टिकोण, सरल, अधिक, अंतिम), and fix the grammar to "
                       "match (gender and verb endings). Write यह/वह, not ये/वो. Never use the 'केवल/सिर्फ़/कोई X नहीं, बल्कि Y' "
                       "construction: say Y directly (for example 'यह कोई साधारण अंत नहीं, बल्कि एक रूपांतरण था' becomes "
                       "'यह एक रूपांतरण था').\n"),
             "English": ("Never use the 'not X, but Y' or 'isn't X; it's Y' construction: say Y directly. When the marked word is a modern "
                         "comparison (the internet, electricity, oxygen, atoms, an orchestra, a blueprint), remove the comparison: say it "
                         "plainly or with an image from the work's own world (fire, rivers, dawn, cattle, the sacrifice). Never swap in a "
                         "synonym of the modern thing (not 'world wide web', 'air', 'particle', 'ensemble', 'plan').\n")}
# Rewrites that need no model: the "not X, but Y" contrast reduced to its point, and Urdu words swapped for Hindi words of the
# same gender (the grammar around them stays right). Words whose Hindi equivalent has another gender are left to the model.
CONTRAST = {
    "English": [(re.compile(r"\b(is|are|was) not an? [^.;]{1,40}\. (?:It|He|She|This) (?:is|are|was) "), r"\1 "),
                (re.compile(r"\b(isn['’]t|is not|aren['’]t|are not|wasn['’]t|was not) (?:about |just |only |merely )?[^.;]{1,50}; "
                            r"(?:it|he|she|they|this)(?:['’]s| is| are| was)\b"),
                 lambda m: {"isn't": "is", "isn’t": "is", "is not": "is", "aren't": "are", "aren’t": "are", "are not": "are",
                            "wasn't": "was", "wasn’t": "was", "was not": "was"}[m.group(1)]),
                (re.compile(r"\bnot (?:just|only|merely|simply) [^.;]{1,60}[;,] (?:but |(?:it['’]s|it is|he is|he['’]s|she is|they are) )"), ""),
                (re.compile(r"\b(isn['’]t|is not|aren['’]t|are not|wasn['’]t|was not) (about )?[^.;,]{1,60}, but (?:about )?"),
                 lambda m: {"isn't": "is", "isn’t": "is", "is not": "is", "aren't": "are", "aren’t": "are", "are not": "are",
                            "wasn't": "was", "wasn’t": "was", "was not": "was"}[m.group(1)] + " " + (m.group(2) or ""))],
    "Hindi": [(re.compile(r"(?:केवल|सिर्फ़|सिर्फ|मात्र|कोई|एक) [^।,]{1,50}नहीं(?: है| हैं| था| थे| थी)?,? (?:बल्कि|वरन्|अपितु) (?:(?:वह|यह|वे|ये) )?"), "")],
}
SAME_GENDER = {"ज़्यादा": "अधिक", "ज्यादा": "अधिक", "आख़िरी": "अंतिम", "आखिरी": "अंतिम", "मुमकिन": "संभव", "ख़ुद": "स्वयं", "खुद": "स्वयं",
               "हमेशा": "सदा", "लेकिन": "परंतु", "मतलब": "अर्थ", "इस्तेमाल": "प्रयोग", "आसान": "सरल", "मुश्किल": "कठिन",
               "ज़रूरी": "आवश्यक", "जरूरी": "आवश्यक", "ख़ास": "विशेष", "खास": "विशेष", "बेशक": "निश्चय ही", "असल में": "वास्तव में",
               "वो": "वह", "जवाब": "उत्तर", "सवाल": "प्रश्न", "सवालों": "प्रश्नों", "हिस्सा": "भाग", "हिस्से": "भाग", "हिस्सों": "भागों",
               "इंसान": "मनुष्य", "इंसानों": "मनुष्यों", "आसमान": "आकाश", "नज़रिया": "दृष्टिकोण", "नजरिया": "दृष्टिकोण", "नज़रिए": "दृष्टिकोण", "नजरिए": "दृष्टिकोण", "म्यूज़ियम": "संग्रहालय", "म्यूजियम": "संग्रहालय"}


def plain_fixes(text: str, language: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for pat, rep in CONTRAST.get(language, []):
        text = pat.sub(rep, text)
    if language == "Hindi":
        for a, b in SAME_GENDER.items():
            text = re.sub(rf"(?<![ऀ-ॿ]){unicodedata.normalize('NFC', a)}(?![ऀ-ॿ])", b, text)
    return re.sub(r"(^|[.!?।]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)


BANNED = {lang: [unicodedata.normalize("NFC", p) for p in pats] for lang, pats in BANNED.items()}


def banned_hits(text: str, language: str) -> List[str]:
    """Stock phrases and loan words in `text`. Devanagari letters with a dot (ज़, फ़, ख़) can be stored as one character or
    two, so both the text and the list are compared in one normal form."""
    text = unicodedata.normalize("NFC", text)
    return [m.group(0) for pat in BANNED.get(language, []) for m in re.finditer(pat, text, re.I)]


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?।])\s+", text.strip()) if s]


def source_excerpt(e: Dict[str, Any], limit: int = 7000) -> str:
    """Up to `limit` characters of the public-domain text itself, when it is on Wikisource (hymns, Upanishads, Hindi classics)."""
    b = e.get("build") or {}
    if not b.get("wikisource"):
        return ""
    try:
        from backend.webapp import catalog
        kw = {"api": b["wikisource_api"]} if b.get("wikisource_api") else {}
        text = catalog.wikisource_text(b["wikisource"], max_parts=3, **kw)
    except Exception:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if re.search(r"[Tt]ranslation revised|revised \d{1,2} \w+ (?:19[7-9]\d|20\d\d)", text):
        return ""       # a modern revision of the translation (Wikisource's share-alike licence, not public domain): never quoted
    return text[:limit]


def _notes(sc: Dict[str, Any]) -> str:
    out = []
    for i, s in enumerate(sc.get("sections") or [], 1):
        out.append(f"{i}. {s.get('heading', '')}: {(s.get('narration') or '')[:900]}")
    if sc.get("thesis"):
        out.insert(0, f"In one sentence: {sc['thesis']}")
    return "\n".join(out)


def _words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def retell(e: Dict[str, Any], sc: Dict[str, Any], language: str, accent: str = "", refresh: bool = False) -> Dict[str, Any]:
    """{"title", "chapters": [{"title", "narration", "quote"}]} for one title in one language (cached)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([sc.get("sections"), language, accent, PROMPT], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    path = CACHE / f"{e['id']}_{language}_{accent or 'x'}_{key}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    total = sum(_words(s.get("narration") or "") for s in sc.get("sections") or [])
    source = source_excerpt(e)
    hymn = bool(source) and bool(re.search(r"\bHYMN [IVXLC]+\b", source))
    if hymn:                                   # about fifty words for each verse, so the whole hymn is told
        verses = 1 + len(re.findall(r"^\s*\d{1,2} \S", source, re.M))
        total = max(total, min(1100, 50 * verses + 60))
    total = max(260, min(1400, total))
    n = max(4, min(9, len(sc.get("sections") or []) + 1))
    open_words = 60 if total < 700 else 90
    words = max(50, (total - open_words) // (n - 1))
    source = source_excerpt(e)
    credit = (e.get("rights") or {}).get("attribution") or e["title"]
    what = f'"{(script_title(e) or e["title"])}" ({credit})'
    source_block = (f"\nThe public-domain text itself (for quotations and details; it is the source, so quote it exactly or not at all):\n"
                    f"<<<\n{source}\n>>>\n") if source else ""
    quote_rule = ("Weave in two or three short quotations (under 20 words each) from the text above, introduced naturally."
                  if source else "Use no quotations you are not sure of.")
    prompt = PROMPT.format(minutes=max(1, round(total / 150)), what=what, notes=_notes(sc), source_block=source_block, quote_rule=quote_rule,
                           banned=", ".join(sorted({re.sub(r"\\[bw]|\(\?:|\)|\*|\?|\\", "", p).split("[")[0].strip("|") for p in BANNED["English"]}))[:900],
                           language_rule=LANGUAGE_RULES[language].format(accent=ACCENT.get(accent, "")), n=n, open_words=open_words, words=words,
                           language=language)
    best: Optional[Dict[str, Any]] = None
    for attempt in range(4):
        try:
            d = llm_client.generate_json(prompt + ACCURACY + (HYMN_RULE if hymn else ""), temperature=0.8, timeout=300, num_ctx=16384)
        except Exception:
            continue
        chapters = [c for c in (d or {}).get("chapters") or [] if (c.get("narration") or "").strip()]
        if len(chapters) < 3:
            continue
        got = sum(_words(c["narration"]) for c in chapters)
        # a draft is scored by its wrong quotations and stock phrases, and by how far it falls short of the length
        score = _problems(chapters, source, language) + max(0, total * 0.8 - got) / 40
        cand = {"title": (d.get("title") or "").strip()[:90], "chapters": chapters, "words": got, "source_quoted": bool(source),
                "score": round(score, 1)}
        if best is None or score < best["score"]:
            best = cand
        if score < 1:
            break
    if best is None:
        raise RuntimeError("no documentary script (every free model failed)")
    if source and language == "English":
        best = _fix_quotes(best, source)
    best = _fix_phrases(best, language)
    if source and language == "English":
        best = mark_verses(best, source)
    if language == "Hindi":
        best = proofread_hindi(best)
    best["banned_left"] = banned_hits(" ".join(c["narration"] for c in best["chapters"]) + " " + best["title"], language)
    if source and language == "English":
        best["misquotes_left"] = misquotes(" ".join(c["narration"] for c in best["chapters"]), source)
    path.write_text(json.dumps(best, ensure_ascii=False, indent=1), encoding="utf-8")
    return best


def _fix_phrases(d: Dict[str, Any], language: str) -> Dict[str, Any]:
    """Rewrites the sentences that use a tell-tale phrase (one request for all of them)."""
    todo = []
    for ci, c in enumerate(d["chapters"]):
        for si, s in enumerate(_sentences(c["narration"])):
            hits = banned_hits(s, language)
            if hits:
                todo.append((ci, si, s, hits[0]))
    if not todo:
        return _plain(d, language)
    items = "\n".join(f"{k + 1}. [{h}] {s}" for k, (_, _, s, h) in enumerate(todo))
    try:
        out = llm_client.generate_json(FIX_PROMPT.format(language=language, items=items, extra=FIX_EXTRA.get(language, "")),
                                       temperature=0.5, timeout=240)
        new = out.get("sentences") or []
    except Exception:
        return _plain(d, language)
    if len(new) != len(todo):
        return _plain(d, language)
    for (ci, si, old, _), fixed in zip(todo, new):
        if fixed and not banned_hits(fixed, language):
            nar = unicodedata.normalize("NFC", d["chapters"][ci]["narration"])
            d["chapters"][ci]["narration"] = nar.replace(unicodedata.normalize("NFC", old), fixed.strip(), 1)
    return _plain(d, language)


def _plain(d: Dict[str, Any], language: str) -> Dict[str, Any]:
    """Whatever the model left: the contrast construction and same-gender loan words, fixed by rule."""
    for c in d["chapters"]:
        c["narration"] = plain_fixes(c["narration"], language)
        c["title"] = plain_fixes(c.get("title") or "", language)
        c["quote"] = plain_fixes(c.get("quote") or "", language)
    d["title"] = plain_fixes(d.get("title") or "", language)
    return d


def script_title(e: Dict[str, Any]) -> str:
    sc = (e.get("scripts") or {}).get("English") or {}
    return sc.get("title") or ""


TRANSLATE_PROMPT = """Translate this documentary narration from English into Hindi, for the Hindi version of the same YouTube video.
{language_rule}
Vedic terms: priest = पुरोहित (Agni as the priest who calls the gods: होता), sacrifice or ritual = यज्ञ, offering = आहुति,
hymn = सूक्त, verse = मंत्र or ऋचा, seer or sage = ऋषि, gods = देवता, the cosmic Law or order (Rta, "Law eternal") = ऋत,
heaven = स्वर्ग, the Rigveda = ऋग्वेद, thousand-headed = हज़ार सिरों वाला (सहस्रशीर्षा).
Keep the meaning, every chapter, the order and every quotation. It must read as if a Hindi writer wrote it: natural Hindi
sentence order, no English idioms carried over word for word, no invented details.
Quotations (in quotation marks) are from {credit}: translate them faithfully and simply, in correct Hindi grammar.
Titles: short and natural in Hindi.
Return only JSON: {{"title": "...", "chapters": [{{"title": "...", "narration": "...", "quote": "the chapter's quotation in Hindi exactly as it appears in the narration, or empty"}}]}}
with exactly {n} chapters in the same order.

ENGLISH:
{source}"""


# sent with the translation prompts (kept out of TRANSLATE_PROMPT so that cached translations keep their keys)
QUOTE_KEEP = ("\nKeep the curly quotation marks “ ” around every translated quotation, exactly where the English has them: the "
              "quoted words are read aloud by a second voice.")


def _requote(en_doc: Dict[str, Any], hi: Dict[str, Any]) -> Dict[str, Any]:
    """Where the English chapter has a quotation and the Hindi one lost its marks, the Hindi quotation is marked again."""
    for en_c, hi_c in zip(en_doc.get("chapters", []), hi.get("chapters", [])):
        had = len([m for m in QUOTED.finditer(en_c.get("narration") or "") if len((m.group(1) or m.group(2) or "").split()) >= 4])
        nar = hi_c.get("narration") or ""
        if not had or "“" in nar or '"' in nar:
            continue
        q = (hi_c.get("quote") or "").strip().strip("“”\"'")
        if len(q.split()) >= 4 and q in nar:
            hi_c["narration"] = nar.replace(q, f"“{q}”", 1)
    return hi


def hindi_from_english(e: Dict[str, Any], en_doc: Dict[str, Any], refresh: bool = False) -> Dict[str, Any]:
    """The Hindi script of a video translated from its checked English script. The free models write English far better than
    Hindi (a script written straight in Hindi had made-up words and broken quotations), and both versions then tell the same
    story. The translation is checked like any other script (stock phrases, English and Urdu words)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([en_doc.get("title"), [c.get("narration") for c in en_doc.get("chapters", [])], TRANSLATE_PROMPT],
                                  ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    path = CACHE / f"{e['id']}_Hindi_fromen_{key}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    credit = (e.get("rights") or {}).get("attribution") or e["title"]
    src = json.dumps({"title": en_doc["title"], "chapters": [{"title": c.get("title", ""), "narration": c["narration"]}
                                                             for c in en_doc["chapters"]]}, ensure_ascii=False, indent=1)
    prompt = TRANSLATE_PROMPT.format(language_rule=LANGUAGE_RULES["Hindi"], credit=credit, source=src, n=len(en_doc["chapters"]))
    best: Optional[Dict[str, Any]] = None
    if sum(_words(c["narration"]) for c in en_doc["chapters"]) > 450:
        best = _translate_by_chapter(en_doc, credit)
    for attempt in range(0 if best else 3):
        try:
            d = llm_client.generate_json(prompt + QUOTE_KEEP, temperature=0.3, timeout=300, num_ctx=16384)
        except Exception:
            continue
        chapters = [c for c in (d or {}).get("chapters") or [] if (c.get("narration") or "").strip()]
        if len(chapters) == len(en_doc["chapters"]):
            best = {"title": (d.get("title") or "").strip()[:90], "chapters": chapters,
                    "words": sum(_words(c["narration"]) for c in chapters), "source_quoted": en_doc.get("source_quoted", False),
                    "translated_from_english": True}
            break
    if best is None:
        best = _translate_by_chapter(en_doc, credit)
    if best is None:
        raise RuntimeError("no Hindi translation of the script")
    best = proofread_hindi(_fix_phrases(best, "Hindi"))
    best = _requote(en_doc, best)
    best["banned_left"] = banned_hits(" ".join(c["narration"] for c in best["chapters"]) + " " + best["title"], "Hindi")
    path.write_text(json.dumps(best, ensure_ascii=False, indent=1), encoding="utf-8")
    return best


PROOF_PROMPT = """Proofread this Hindi narration for a documentary. Correct only mistakes: grammar, gender agreement (for example
वेदी, अग्नि-ज्वाला, सृष्टि are feminine), verb endings, postpositions, spelling and clumsy repetitions such as "वह वह".
Keep the meaning, the words, the order and the length; keep every quotation; keep it pure, standard Hindi with no English
and no Urdu words. Return only JSON: {{"chapters": ["corrected narration", ...]}} with exactly {n} items in the same order.

{items}"""


def proofread_hindi(d: Dict[str, Any]) -> Dict[str, Any]:
    """A last pass over a Hindi script by a second request: grammar, gender and spelling only. Kept only when it returns every
    chapter, no chapter changes length much, and it brings back no stock phrase or loan word."""
    items = "\n\n".join(f"{k + 1}. {c['narration']}" for k, c in enumerate(d["chapters"]))
    try:
        out = llm_client.generate_json(PROOF_PROMPT.format(n=len(d["chapters"]), items=items), temperature=0.2, timeout=300, num_ctx=16384)
        new = [x for x in (out or {}).get("chapters") or [] if isinstance(x, str)]
    except Exception:
        return d
    if len(new) != len(d["chapters"]):
        return d
    for c, fixed in zip(d["chapters"], new):
        fixed = plain_fixes(fixed.strip(), "Hindi")
        if fixed and 0.8 < len(fixed) / max(1, len(c["narration"])) < 1.25 and not banned_hits(fixed, "Hindi"):
            c["narration"] = fixed
    d["proofread"] = True
    return d


CHAPTER_PROMPT = """Translate this one chapter of a documentary narration from English into Hindi.
{language_rule}
It must read as if a Hindi writer wrote it: natural Hindi sentence order, no English idioms carried over word for word, no
invented details. Names stay as they are in the story, written in Devanagari. Quotations, if any, are from {credit}:
translate them faithfully.
Return only JSON: {{"title": "the chapter title in Hindi", "narration": "the whole chapter in Hindi"}}

CHAPTER TITLE: {title}
CHAPTER:
{narration}"""
TITLE_PROMPT = """Translate this documentary title into short, natural, pure Hindi (no English and no Urdu words).
Return only JSON: {{"title": "..."}}

{title}"""


def _translate_by_chapter(en_doc: Dict[str, Any], credit: str) -> Optional[Dict[str, Any]]:
    """The Hindi translation one chapter per request: slower, but a long script no longer has to fit in one reply."""
    chapters = []
    for c in en_doc["chapters"]:
        got = None
        for attempt in range(3):
            try:
                d = llm_client.generate_json(CHAPTER_PROMPT.format(language_rule=LANGUAGE_RULES["Hindi"], credit=credit,
                                                                   title=c.get("title", ""), narration=c["narration"]) + QUOTE_KEEP,
                                             temperature=0.3, timeout=240)
            except Exception:
                continue
            text = ((d or {}).get("narration") or "").strip()
            if text and 0.6 < len(text) / max(1, len(c["narration"])) < 2.6:      # Hindi runs a little longer than English
                got = {"title": ((d or {}).get("title") or "").strip(), "narration": text, "quote": ""}
                break
        if got is None:
            return None
        chapters.append(got)
    title = ""
    try:
        title = ((llm_client.generate_json(TITLE_PROMPT.format(title=en_doc["title"]), temperature=0.3, timeout=120) or {}).get("title") or "").strip()
    except Exception:
        pass
    return {"title": title[:90] or chapters[0]["title"], "chapters": chapters, "words": sum(_words(c["narration"]) for c in chapters),
            "source_quoted": en_doc.get("source_quoted", False), "translated_from_english": True, "by_chapter": True}


VERSES_PROMPT = """Translate these verses of a Vedic hymn from Ralph T. H. Griffith's English translation (1889-1892) into Hindi.
{language_rule}
Vedic terms: priest = पुरोहित (Agni as the priest who calls the gods: होता), sacrifice or ritual = यज्ञ, offering = आहुति,
hymn = सूक्त, verse = मंत्र, seer or sage = ऋषि, gods = देवता, the cosmic Law or order (Rta, "Law eternal") = ऋत, heaven = स्वर्ग.
Keep the names of gods and sages as they are traditionally written in Hindi (अग्नि, इन्द्र, वरुण, सोम, पुरुष, विराट्, यम).
Each verse stays one verse: faithful to its meaning, dignified and clear, in simple pure Hindi prose (not rhymed), nothing added,
with complete and correct grammar. Translate numbers exactly ("thrice seven" is इक्कीस, not सात बार सात). Griffith's old English:
"hath" = has, "kine" = cows, "thence" = from there, "what eats and what eats not" = the living and the lifeless. Names:
Sadhyas = साध्य, Rsis = ऋषि, Viraj = विराट्, Rcas = ऋचाएँ, Sama = साम, Yajus = यजुर्वेद, Vayu = वायु, Savitar = सविता. Terms: the altar's fencing-sticks = परिधियाँ; Griffith's "spells and charms" (the metres) = छंद; "cattle with two rows of teeth" = जिनके दोनों जबड़ों में दाँत हैं, ऐसे पशु. Speak of the god or being addressed in one consistent, respectful form throughout (वे, उनके, उन्होंने).
Return only JSON: {{"verses": ["...", ...]}} with exactly {n} items, in the same order.

{items}"""


def translate_verses(e: Dict[str, Any], verses: List[str], refresh: bool = False) -> Dict[str, Any]:
    """A hymn's verses in pure Hindi, ten verses per request, each checked like a script (loan words fixed by rule)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([verses, VERSES_PROMPT], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    path = CACHE / f"{e['id']}_Hindi_verses_{key}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    out: List[str] = []
    for i in range(0, len(verses), 10):
        chunk = verses[i:i + 10]
        items = "\n".join(f"{k + 1}. {v}" for k, v in enumerate(chunk))
        got = None
        for attempt in range(3):
            try:
                d = llm_client.generate_json(VERSES_PROMPT.format(language_rule=LANGUAGE_RULES["Hindi"], n=len(chunk), items=items),
                                             temperature=0.3, timeout=240)
            except Exception:
                continue
            vs = [x for x in (d or {}).get("verses") or [] if isinstance(x, str) and x.strip()]
            if len(vs) == len(chunk):
                got = [plain_fixes(x.strip(), "Hindi") for x in vs]
                break
        if got is None:
            raise RuntimeError("no Hindi translation of the verses")
        out += got
    out = _review_verses_hi(verses, out)
    d = {"verses": out, "banned_left": banned_hits(" ".join(out), "Hindi"), "reviewed": True}
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


OCR_PROMPT = """These are the verses of one hymn in Ralph T. H. Griffith's English translation of the Rigveda (1889-1892), taken from a
scanned book. Correct only scanning mistakes: misread letters (for example "cats" where Griffith wrote "eats", "car" where he wrote
"ear"), words wrongly joined or split, and punctuation missing between clauses. Keep Griffith's own old wording exactly ("hath",
"kine", "thence", "thou"), his spelling of names, and each verse's words, meaning and length. A verse without a scanning mistake
is returned unchanged.
Return only JSON: {{"verses": ["...", ...]}} with exactly {n} items, in the same order.

{items}"""
REVIEW_PROMPT = """Each item is a verse of a Vedic hymn in Griffith's English, then our Hindi translation of it. Check every Hindi verse
against its English and correct it only where it is wrong: a changed or lost meaning, a wrong number or name, broken grammar, or an
English or Urdu word. Names: Sadhyas = साध्य, Rsis = ऋषि, Viraj = विराट्, Vayu = वायु. Terms: परिधियाँ for the altar's fencing-sticks, छंद for Griffith's "spells and charms"; one consistent, respectful form (वे, उनके) for the god or being addressed. Keep correct verses exactly as they are, in
pure, dignified Hindi.
Return only JSON: {{"verses": ["the Hindi verse, corrected or unchanged", ...]}} with exactly {n} items, in the same order.

{items}"""


def proofread_verses_en(e: Dict[str, Any], verses: List[str], refresh: bool = False) -> List[str]:
    """Griffith's verses with scanning mistakes corrected. A correction is kept only when it is small (a letter or a comma,
    not a rewrite), so his own wording is never modernised."""
    from difflib import SequenceMatcher
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([verses, OCR_PROMPT], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    path = CACHE / f"{e['id']}_English_verses_{key}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))["verses"]
    out: List[str] = []
    for i in range(0, len(verses), 10):
        chunk = verses[i:i + 10]
        items = "\n".join(f"{k + 1}. {v}" for k, v in enumerate(chunk))
        fixed = None
        for attempt in range(3):
            try:
                d = llm_client.generate_json(OCR_PROMPT.format(n=len(chunk), items=items), temperature=0.1, timeout=240)
            except Exception:
                continue
            vs = [x for x in (d or {}).get("verses") or [] if isinstance(x, str) and x.strip()]
            if len(vs) == len(chunk):
                fixed = [new.strip() if SequenceMatcher(None, old, new).ratio() >= 0.93 else old for old, new in zip(chunk, vs)]
                break
        out += fixed or chunk
    changed = [(a, b) for a, b in zip(verses, out) if a != b]
    path.write_text(json.dumps({"verses": out, "changed": changed}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _review_verses_hi(verses_en: List[str], verses_hi: List[str]) -> List[str]:
    """A second request checks each Hindi verse against its English and corrects only what is wrong."""
    out: List[str] = []
    for i in range(0, len(verses_en), 8):
        en, hi = verses_en[i:i + 8], verses_hi[i:i + 8]
        items = "\n".join(f"{k + 1}. EN: {a}\n   HI: {b}" for k, (a, b) in enumerate(zip(en, hi)))
        got = None
        for attempt in range(2):
            try:
                d = llm_client.generate_json(REVIEW_PROMPT.format(n=len(en), items=items), temperature=0.1, timeout=240)
            except Exception:
                continue
            vs = [x for x in (d or {}).get("verses") or [] if isinstance(x, str) and x.strip()]
            if len(vs) == len(en):
                got = []
                for old, new in zip(hi, vs):
                    new = plain_fixes(new.strip(), "Hindi")
                    got.append(new if not banned_hits(new, "Hindi") and 0.6 < len(new) / max(1, len(old)) < 1.6 else old)
                break
        out += got or hi
    return out
