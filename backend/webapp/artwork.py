"""
Public-domain paintings from Wikimedia Commons as moving backgrounds (a slow pan and zoom) for the YouTube videos.

Only files marked public domain or CC0 are used, at least 1,200 pixels wide, JPEG or PNG. Each painting's title and
artist go into the video's description. A title-matched search comes first (for example "Agni painting"), then a
curated pool per series (Raja Ravi Varma, Kalighat, Pahari and Rajput paintings, ...), picked the same way every time
for the same video. Downloads (at most 2,400 pixels wide) are kept in a small cache.
"""
import hashlib
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.config import TEMP_DIR
from backend.video_engine import rights

API = "https://commons.wikimedia.org/w/api.php"
CACHE = TEMP_DIR / "artwork"
CACHE_MB = 400
POOLS = {
    "vedas": ["Raja Ravi Varma painting", "Hindu deity painting 19th century", "Kangra painting", "Pahari painting", "Kalighat painting",
              "Vishnu painting 19th century", "Shiva painting 19th century", "Hindu sage painting", "Ganges painting 19th century"],
    "classics": ["Raja Ravi Varma painting", "Rajput painting", "Mughal painting", "Kangra painting", "Company painting India",
                 "Indian miniature painting", "Kalighat painting", "Indian landscape painting 19th century"],
}
NOT_ART = re.compile(r"\b(map|coin|stamp|logo|diagram|chart|flag|seal|signature|poster|photograph of the author|photo|complex|DSC\w*|IMG\w*)\b"
                     r"|^\d{5,}", re.I)          # photographs of places and camera file names are not paintings
FREE = re.compile(r"public domain|^pd\b|pd-|cc0", re.I)
NOT_SACRED = re.compile(r"\b(portrait|lady|papa|woman|women|girl|family|maharaja|maharani|rani|queen|king|nawab|british|officer|sons?|darbar|durbar|court|ruled|ruler|raja (?!ravi)\w+|singh"
                        r"|nats?|burm\w*|thagyamin|byamma|thukanda|thuriya)\b", re.I)   # Burmese spirit-god plates are another tradition
INDIAN = re.compile(r"ravi varma|ragamala|ragini|nayika|rajasthan|bundi|kishangarh|awadh|lucknow|bengal|calcutta|deccan|kangra|pahari|kalighat|rajput|mughal|mandi|guler|basohli|mewar|jaipur|india|hindu|vishnu|shiva|krishna|rama|"
                    r"brahma|devi|ganga|ganesh|lakshmi|saraswati|indra|agni|surya|sage|rishi|avatar|puran|veda|temple|punjab", re.I)
# Vedas videos: the painting's own title must show a god, goddess, sage, avatar, temple or sacred river
SACRED = re.compile(r"vishnu|shiva|siva|brahma|devi|goddess|\bgods?\b|deity|deities|krishna|rama\b|agni|indra|surya|varuna|soma|ganga|ganges|"
                    r"sage|rishi|avatar|avatara|incarnation|puran|veda|temple|lakshmi|saraswati|ganesh|ganesa|hanuman|kali\b|durga|parvati|"
                    r"narasimha|vamana|matsya|kurma|varaha|trivikrama|garuda|nandi|yajna|sacrifice|creation|cosmic|hindu", re.I)
# Vedas videos: the gods of the hymns, the sages and the fire sacrifice. The later traditions (Kali, Krishna, Rama, Ganesha;
# Jain and Buddhist art), other countries and sad scenes (funerals, killings) belong to other stories, however beautiful.
VEDIC_POOL = ["Agni painting", "Agni god of fire", "Indra painting", "Indradeva", "Surya painting", "Surya in his chariot", "Varuna painting",
              "Vayu deva", "rishi painting", "Hindu sage painting", "yajna painting", "Vishvamitra painting", "Saraswati painting"]
VEDIC = re.compile(r"agni|indra|surya|sūrya|varuna|vayu|soma\b|ushas|rishi|sage|hermit|sacrific|yajna|yagna|vishvamitra|angiras|marichi|"
                   r"saraswati|sarasvati|vishvarupa|vishnu|rudra|shiva|siva|god of|deva\b|brahmin|vedic|veda", re.I)
POST_VEDIC = re.compile(r"kali\b|durga|chhinnamasta|radha|krishna|gopi|ganesh|ganesa|ganapati|hanuman|\brama\b|\bram\b|sita\b|lakshman|"
                        r"rawana|ravana|lakshmi|parvati|buddha|mandala|jina\b|jain|mahavira|rishabh|tirthankar|japan|muromachi|venice|canale|"
                        r"funeral|cremation|\bsati\b|suttee|pariksha|daksha|manthan|medicine|huqqa|hookah|musician|vina\b|ragini|akbar|"
                        r"\bkhan\b|narada|asura|jambha|killing|destruction|kedar|goddess worshi|roerich|yoga|bamiyan|bamyan", re.I)
# stories and books: the title must name India or an Indian school, place or subject; a bare "Indian" is not enough
STORY_INDIA = re.compile(r"\bindia\b|indian miniature|ravi varma|kangra|pahari|kalighat|rajput|mughal|mandi|guler|basohli|mewar|jaipur|"
                         r"bikaner|bundi|kota|kishangarh|marwar|jodhpur|udaipur|rajasthan|awadh|oudh|lucknow|bengal|calcutta|kolkata|"
                         r"patna|murshidabad|deccan|golconda|hyderabad|mysore|tanjore|thanjavur|madras|punjab|lahore|delhi|agra|"
                         r"benares|varanasi|company painting|ragamala|ragini|nayika|hindola|krishna|radha|rama\b|sita|hindu|"
                         r"indra|vishnu|shiva|ganga|sage|rishi|puran|bhagavata|gita govinda", re.I)
# "Indian" in a title can mean Native American; and portraits of officers and royals belong to no story
NOT_INDIA = re.compile(r"encampment|indian summer|american|sioux|tipi|wigwam|native|tavernier|jacobi|catlin|bierstadt|comanche|apache|"
                       r"navajo|mohawk|cherokee|iroquois|plains indian|red indian", re.I)
NOT_STORY = re.compile(r"ruled|grandsons|main centers|centres of|orfeo|orpheus|keechaka|kichaka|ravana|rawana|slay|demon|killing|glacier|canoe|alaska|portrait|officer|maharani|maharaja|nawab|governor|general|\blord\b|\blady\b|\bsir\b|company officer|battle|postcard|europeans|east-india|east india|woolwich|revenue|fort william|kali\b", re.I)
# never in any video: death and violence shown for their own sake
GRIM = re.compile(r"funeral|cremation|\bsati\b|suttee|sever(?:ed|ing)|behead|killing|corpse|execution|slaughter|torture|massacre", re.I)
# the hymn's deity (or its seer), as found in its Sanskrit index, and what to search for
DEITY_EN = [("अग्नि", "Agni"), ("इन्द्र", "Indra"), ("उष", "Ushas"), ("सूर्य", "Surya"), ("सवित", "Surya"), ("वरुण", "Varuna"),
            ("मित्र", "Surya"), ("सोम", "Soma"), ("पुरुष", "Vishvarupa"), ("विष्णु", "Vishnu"), ("रुद्र", "Rudra"), ("वायु", "Vayu"),
            ("वात", "Vayu"), ("सरस्वती", "Saraswati"), ("वाक्", "Saraswati"), ("पर्जन्य", "Indra")]
SEER_EN = [("वागाम्भृणी", "Saraswati")]           # Vak, who speaks her own hymn (10.125)


def subjects_for(card: Optional[Dict[str, Any]]) -> List[str]:
    """English search names for a hymn from its Sanskrit index card (deity first, then the seer)."""
    devata, rishi = (card or {}).get("devata", ""), (card or {}).get("rishi", "")
    out: List[str] = []
    for words, text in ((DEITY_EN, devata), (SEER_EN, rishi)):
        for dev, en in words:
            if dev in text and en not in out:
                out.append(en)
    return out


# words that museums and uploaders add to every title; left out when telling paintings apart
BOILERPLATE = {"india", "indian", "calcutta", "kalighat", "painting", "paintings", "century", "cleveland", "museum", "circa", "late", "early",
               "company", "school", "collection", "google", "project", "wikimedia", "commons", "with", "from", "their", "national", "delhi",
               "gallery", "fund", "cynthia", "hazen", "polsky", "leon", "metropolitan", "punjab", "pahari", "kangra", "rajput", "mughal",
               "style", "folio", "page", "album", "opaque", "watercolour", "watercolor", "gold", "paper", "cropped", "crop", "detail"}
STOP = {"the", "and", "of", "essence", "explained", "summary", "birth", "divine", "human", "bridge"}


def search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        d = httpx.get(API, headers=rights.UA, timeout=40, params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": limit,
            "prop": "imageinfo", "iiprop": "extmetadata|size|url", "iiurlwidth": 2400,
            "iiextmetadatafilter": "LicenseShortName|Artist|ObjectName"}).json()
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for p in ((d.get("query") or {}).get("pages") or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = re.sub(r"<[^>]+>", "", (meta.get("LicenseShortName") or {}).get("value", "")).strip()
        title = p.get("title", "")[5:]
        if not FREE.search(lic) or "cc-by" in lic.lower() or ii.get("width", 0) < 1200:
            continue
        if not title.lower().endswith((".jpg", ".jpeg", ".png")) or NOT_ART.search(title):
            continue
        artist = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (meta.get("Artist") or {}).get("value", ""))).strip()[:80]
        artist = re.sub(r"^(.+?)\s+\1$", r"\1", artist)          # "Unknown author Unknown author"
        if re.search(r"permission|licen[cs]e|released|copyright", artist, re.I):     # a rights note, not a name
            artist = ""
        out.append({"title": re.sub(r"\.(jpe?g|png)$", "", title, flags=re.I), "file": title, "url": ii.get("thumburl") or ii.get("url"),
                    "license": lic, "artist": artist or "unknown artist", "page": ii.get("descriptionurl")})
    return out


def pick(series: str, hint: str, k: int = 5, subjects: Optional[List[str]] = None, avoid: Optional[str] = None) -> List[Dict[str, Any]]:
    """Up to k public-domain paintings for a video: the subject's own (the hymn's deity) and title-matched ones first, then
    the series' pool."""
    if series == "lectures":
        return []                                   # a lecture gets a moving gradient, not paintings
    found: Dict[str, Dict[str, Any]] = {}
    for sub in subjects or []:
        for q in ((f"{sub} painting", f"{sub} god") if series == "vedas" else (f"{sub} painting", sub)):
            for img in search(q, 20):
                found.setdefault(img["file"], img)
    words = " ".join([w for w in re.findall(r"[A-Za-z]{3,}", hint) if w.lower() not in STOP][:4])
    if words:
        for img in search(f"{words} {'Hindu ' if series == 'vedas' else ''}painting", 12):
            found.setdefault(img["file"], img)
    rng = random.Random(hashlib.sha1((series + hint).encode("utf-8")).hexdigest())
    pool = list(VEDIC_POOL if series == "vedas" else POOLS.get(series, POOLS["classics"]))
    rng.shuffle(pool)
    for q in pool:
        if len([1 for x in found.values() if series != "classics" or (STORY_INDIA.search(x["title"]) and not NOT_INDIA.search(x["title"])
                                                                         and not NOT_STORY.search(x["title"]))]) >= k * 2:
            break
        for img in search(q, 15):
            found.setdefault(img["file"], img)
    if series == "vedas":                           # the Vedic gods, sages and sacrifice; not portraits, later traditions or sad scenes
        subs = " ".join(subjects or []).lower()
        extra = re.compile("|".join(x for x, keep in ((r"shiva|siva", "rudra"), (r"vishnu", "vish")) if keep not in subs) or "$^", re.I)
        found = {f: img for f, img in found.items() if VEDIC.search(img["title"]) and not POST_VEDIC.search(img["title"])
                 and not NOT_SACRED.search(img["title"]) and not extra.search(img["title"])}
    found = {f: img for f, img in found.items() if not GRIM.search(img["title"])}
    if avoid:                                       # a story can rule out what does not belong in it
        found = {f: img for f, img in found.items() if not re.search(avoid, img["title"], re.I)}
    if series == "classics":                        # Indian stories get Indian paintings (not European princes out stalking deer)
        found = {f: img for f, img in found.items() if STORY_INDIA.search(img["title"]) and not NOT_INDIA.search(img["title"])
                 and not NOT_STORY.search(img["title"])}

    def subject(title: str) -> set:
        return {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in BOILERPLATE}

    unique: Dict[str, Dict[str, Any]] = {}
    for f, img in found.items():                    # the same painting uploaded twice under different names counts once
        mine = subject(img["title"])
        dup = False
        for o in unique.values():
            other = subject(o["title"])
            small = min(len(mine), len(other))
            if (small >= 3 and len(mine & other) >= 0.8 * small) or (mine and mine == other):
                dup = True
                break
        if not dup:
            unique[f] = img
    found = unique
    keys = [w for sub in (subjects or []) for w in re.findall(r"[a-z]{4,}", sub.lower()) if w not in ("painting", "god")]
    keys += [w.lower() for w in words.split()]
    first = [f for f in found.values() if any(w in f["title"].lower() for w in keys)]
    rest = [f for f in found.values() if f not in first]
    rng.shuffle(rest)
    return (first + rest)[:k]


def fetch(img: Dict[str, Any]) -> Path:
    """The painting as a local file (cached)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    ext = ".png" if img["url"].lower().endswith(".png") else ".jpg"
    path = CACHE / (hashlib.sha1(img["url"].encode("utf-8")).hexdigest()[:20] + ext)
    if not path.exists():
        with httpx.stream("GET", img["url"], headers=rights.UA, timeout=90, follow_redirects=True) as r:
            r.raise_for_status()
            data = b""
            for chunk in r.iter_bytes(1 << 20):
                data += chunk
                if len(data) > 25 * 1024 * 1024:
                    raise RuntimeError("image too large")
        path.write_bytes(data)
        _trim()
    else:
        path.touch()
    return path


def _trim() -> None:
    files = sorted(CACHE.glob("*"), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    while files and total > CACHE_MB * 1024 * 1024:
        f = files.pop(0)
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def credit(img: Dict[str, Any]) -> str:
    return f"{img['title']} — {img['artist']}, Wikimedia Commons, {img['license']}"
