"""
The Vedas and Upanishads, explained in Hindi, from public-domain English translations on English Wikisource:

  Rigveda     Ralph T. H. Griffith, "The Hymns of the Rigveda" (1889-1892; Griffith died in 1906): one title per hymn,
              the best-known hymns first (the creation hymn, Purusha Sukta, the hymns with the Gayatri and
              Mahamrityunjaya mantras, ...).
  Upanishads  F. Max Müller, "Sacred Books of the East", volumes 1 and 15 (1879-1884; Müller died in 1900): one title
              per Upanishad.
Both translators died long ago, so these are public domain in India, the US and the UK. The explainers present each
text's meaning and setting, name the translator, and note where traditional and scholarly readings differ.
"""
import unicodedata
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from backend.webapp.catalog import _entry, _title_key, _ws_subpages
from backend.webapp.sources_intl import EN_WIKISOURCE

RIGVEDA = "The Hymns of the Rigveda"
UPANISHAD_VOLUMES = ("Sacred Books of the East/Volume 1", "Sacred Books of the East/Volume 15")
FAMOUS_HYMNS = {   # (book, hymn) -> the name it is known by
    (10, 129): "नासदीय सूक्त (सृष्टि का सूक्त)", (10, 90): "पुरुष सूक्त", (3, 62): "गायत्री मंत्र वाला सूक्त",
    (10, 121): "हिरण्यगर्भ सूक्त", (10, 125): "वाक् सूक्त", (7, 59): "महामृत्युंजय मंत्र वाला सूक्त",
    (10, 191): "संज्ञान सूक्त (एकता का सूक्त)", (1, 1): "अग्नि सूक्त (ऋग्वेद का पहला सूक्त)", (1, 164): "अस्य वामस्य (पहेलियों का सूक्त)",
    (10, 34): "अक्ष सूक्त (जुआरी का पछतावा)", (10, 117): "दान की महिमा का सूक्त", (5, 85): "वरुण सूक्त", (10, 10): "यम-यमी संवाद",
    (1, 89): "आ नो भद्राः (शुभ विचारों का सूक्त)",
}
UPANISHAD_NAMES = (("khandogya", "छान्दोग्य उपनिषद"), ("talavakara", "केन उपनिषद (तलवकार)"), ("aitareya", "ऐतरेय आरण्यक और उपनिषद"),
                   ("kaushitaki", "कौषीतकि उपनिषद"), ("kaushîtaki", "कौषीतकि उपनिषद"), ("vâgasaneyi", "ईश उपनिषद (वाजसनेयी संहिता)"),
                   ("vagasaneyi", "ईश उपनिषद (वाजसनेयी संहिता)"), ("katha", "कठ उपनिषद"), ("mundaka", "मुण्डक उपनिषद"),
                   ("prasña", "प्रश्न उपनिषद"), ("prasna", "प्रश्न उपनिषद"))


def _url(page: str) -> str:
    return "https://en.wikisource.org/wiki/" + quote(page.replace(" ", "_"))


def _vedic_entry(title: str, page: str, translator: str, died: int, credit: str, desc: str, priority: float,
                 min_words: int) -> Dict[str, Any]:
    e = _entry("book", title, _url(page), {"backend": "none", "url": _url(page)}, "Public domain", credit, translator, None, desc,
               priority=priority, build={"wikisource": page, "wikisource_api": EN_WIKISOURCE, "language": "Hindi",
                                         "yt_series": "vedas", "min_words": min_words})
    e["rights"]["death_year"] = died
    return e


def harvest(seen: set, progress: Callable[[str], None], save: Callable[[Dict[str, Any]], None], want: Optional[int] = None) -> int:
    added = 0
    progress("Vedas: listing the Rigveda hymns...")
    for page in _ws_subpages(RIGVEDA, EN_WIKISOURCE):
        parts = page.split("/")
        if len(parts) != 3 or not parts[1].startswith("Book ") or not parts[2].startswith("Hymn "):
            continue
        try:
            book, hymn = int(parts[1][5:]), int(parts[2][5:])
        except ValueError:
            continue
        name = FAMOUS_HYMNS.get((book, hymn))
        title = f"ऋग्वेद {book}.{hymn}" + (f" — {name}" if name else "")
        key = _title_key("book", title, "rigveda")
        if _url(page) in seen or key in seen:
            continue
        e = _vedic_entry(title, page, "Ralph T. H. Griffith (translator)", 1906,
                         f'Rigveda, book {book}, hymn {hymn}, translated by Ralph T. H. Griffith ("The Hymns of the Rigveda", 1889-1892), '
                         "via Wikisource; public domain",
                         f"ऋग्वेद के मण्डल {book} का सूक्त {hymn}, सरल हिंदी में समझाया गया।",
                         60.0 if name else 12.0 + (2.0 if book in (1, 10) else 0.0), 120)
        save(e)
        seen.update((_url(page), key))
        added += 1
        if want is not None and added >= want:
            return added
    progress(f"Vedas: {added} Rigveda hymns; now the Upanishads...")
    for vol in UPANISHAD_VOLUMES:
        for page in _ws_subpages(vol, EN_WIKISOURCE):
            rest = page[len(vol) + 1:]
            if "/" in rest or "introduction" in rest.lower():
                continue                               # chapters of an Upanishad are read with it; the introduction is Müller's own
            folded = unicodedata.normalize("NFKD", rest).encode("ascii", "ignore").decode().lower()   # "Khândogya" -> "khandogya"
            name = next((hi for k, hi in UPANISHAD_NAMES if k in folded or k in rest.lower()), None)
            if not name:
                continue
            key = _title_key("book", name, "upanishad")
            if _url(page) in seen or key in seen:
                continue
            e = _vedic_entry(name, page, "F. Max Müller (translator)", 1900,
                             f'"{rest}", translated by F. Max Müller, Sacred Books of the East ({vol.rsplit("/", 1)[-1]}), via Wikisource; public domain',
                             f"{name}: इसकी मुख्य बातें और कहानियाँ सरल हिंदी में।", 55.0, 300)
            save(e)
            seen.update((_url(page), key))
            added += 1
    progress(f"Vedas and Upanishads: {added} titles added")
    return added


def famous_ids(entries: List[Dict[str, Any]]) -> List[str]:
    """The best-known hymns and every Upanishad, for the builder to make first."""
    return [e["id"] for e in entries if (e.get("build") or {}).get("yt_series") == "vedas" and (e.get("priority") or 0) >= 55]
