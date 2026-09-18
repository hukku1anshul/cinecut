"""
Second-language versions of library titles. The finished summary is translated (a few AI requests) instead of the
source being summarised again: Hindi summaries get English for viewers in the US and the UK, and English summaries of
US and UK sources get Hindi where the title is cleared in India.
"""
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.video_engine import llm_client
from backend.webapp import library as lib

SCRIPT_PROMPT = """Translate this summary script from {src} into natural, simple {dst}, as a narrator would say it.
Keep the meaning, order and structure exactly; do not add or remove information; keep people's names consistent.
Return JSON with exactly the same keys, and the same number of items in every list.

{payload}"""
NARRATION_PROMPT = """Translate each of these {n} narration passages from {src} into natural, simple spoken {dst}.
Keep the meaning exactly; do not add or remove information; keep people's names consistent.
Return JSON: {{"narrations": ["...", ...]}} with exactly {n} items, in the same order.

{payload}"""


def _devanagari_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(1 for c in letters if "ऀ" <= c <= "ॿ") / max(1, len(letters))


def _in_language(text: str, lang: str) -> bool:
    share = _devanagari_share(text)
    if lang == "English":
        return share < 0.05
    if lang in ("Hindi", "Marathi"):
        return share > 0.6
    return True


def _ask(prompt: str, api_key: Optional[str]) -> Any:
    return llm_client.generate_json(prompt, api_key=api_key, temperature=0.1, timeout=240)


def translate_script(entry: Dict[str, Any], src: str, dst: str, api_key: Optional[str] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    sc = (entry.get("scripts") or {}).get(src) or entry["script"]
    pieces = (entry.get("pieces") or {}).get(src) or []
    meta = {"title": sc.get("title") or entry.get("title") or "", "thesis": sc.get("thesis") or "", "hook": sc.get("hook") or "",
            "intro": " ".join(p["text"] for p in pieces if p.get("section") == -1),
            "sections": [{"heading": s.get("heading") or "", "key_points": s.get("key_points") or []} for s in sc["sections"]],
            "takeaways": sc.get("takeaways") or [], "glossary": sc.get("glossary") or [], "quiz": sc.get("quiz") or []}
    out = _ask(SCRIPT_PROMPT.format(src=src, dst=dst, payload=json.dumps(meta, ensure_ascii=False)), api_key)
    if not isinstance(out, dict) or len(out.get("sections") or []) != len(meta["sections"]):
        raise RuntimeError("the translation did not keep the script's sections")
    narrations: List[str] = []
    secs = sc["sections"]
    for i in range(0, len(secs), 3):
        chunk = [s.get("narration") or "" for s in secs[i:i + 3]]
        got = _ask(NARRATION_PROMPT.format(src=src, dst=dst, n=len(chunk), payload=json.dumps({"narrations": chunk}, ensure_ascii=False)),
                   api_key)
        items = got.get("narrations") if isinstance(got, dict) else None
        if not isinstance(items, list) or len(items) != len(chunk):
            raise RuntimeError("the narration translation came back incomplete")
        narrations += [" ".join(str(x or "").split()) for x in items]
    if not all(narrations) or not _in_language(" ".join(narrations), dst):
        raise RuntimeError(f"the narration did not come back in {dst}")
    sections = [{"heading": str(t.get("heading") or s.get("heading") or ""), "key_points": t.get("key_points") or s.get("key_points") or [],
                 "narration": n} for s, t, n in zip(secs, out["sections"], narrations)]
    script = dict(sc, title=out.get("title") or sc.get("title"), thesis=out.get("thesis") or "", hook=out.get("hook") or "",
                  sections=sections, takeaways=out.get("takeaways") or [], glossary=out.get("glossary") or [], quiz=out.get("quiz") or [])
    pcs = [{"section": -1, "text": p} for p in lib._pieces(out.get("intro") or "")]
    pcs += [{"section": i, "text": p} for i, s in enumerate(sections) for p in lib._pieces(s["narration"])]
    return script, pcs


def translate_title(entry: Dict[str, Any], dst: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Adds a `dst` version of a ready title (script titles: translated script and pieces; films: narration lines)."""
    e = lib.get(entry["id"]) or entry
    langs = e.get("languages") or []
    if dst in langs:
        return e
    if not langs:
        raise RuntimeError("the title has no summary yet")
    src = lib.primary_language(e)
    if e.get("script"):
        script, pcs = translate_script(e, src, dst, api_key)
        scripts = dict(e.get("scripts") or {})
        scripts.setdefault(src, e["script"])
        scripts[dst] = script
        e["scripts"] = scripts
        e["pieces"] = dict(e.get("pieces") or {}, **{dst: pcs})
    else:
        from backend.video_engine.gap_narrator import _translate_batch
        lines = (e.get("narration") or {}).get(src) or []
        texts: List[Optional[str]] = []
        for i in range(0, len(lines), 40):
            texts += _translate_batch([n["text"] for n in lines[i:i + 40]], dst, api_key)
        new = [{"clip": n["clip"], "text": t} for n, t in zip(lines, texts) if t]
        if not lines or len(new) < 0.7 * len(lines):
            raise RuntimeError("too few narration lines could be translated")
        e["narration"] = dict(e.get("narration") or {}, **{dst: new})
    e["primary_language"] = src
    e["languages"] = sorted(set(langs) | {dst})
    b = e.setdefault("build", {})
    b["translated"] = dict(b.get("translated") or {}, **{dst: time.time()})
    return lib.save(e)
