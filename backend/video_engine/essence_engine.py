import re
from pathlib import Path
from typing import List, Dict, Any, Optional

# =============================================================================
# CANONICAL ESSENCE DATABASE - how to add a film
# -----------------------------------------------------------------------------
# Key: the film's lowercase title. Optional "aliases": other spellings.
# Each milestone needs "id", "act", "title", "description", "importance" (0-100) and ONE timing style:
#   1. "target_percent": 0.32  -> position in the runtime; works for any copy of the film.
#   2. "start_sec"/"end_sec"   -> hand-timed on one file. Then ALSO set the film-level
#      "reference_duration_sec" to that file's exact length; copies with a different length are
#      scaled proportionally (approximate), and the UI warns the user.
# Optional narration spoken by the narrator: "narration_hi", "narration_en".
# Unlisted films use Gemini (if an API key is given) or a generic three-act blueprint.
# =============================================================================
OFFLINE_ESSENCE_DATABASE: Dict[str, Dict[str, Any]] = {
    "mr. india": {
        "title": "Mr. India (1987)",
        "aliases": ["mr india", "mister india"],
        "reference_duration_sec": 10740.0,
        "reference_source": "YouTube upload swMmYzuxYT4 (2:59:00). Other copies are scaled.",
        "director": "Shekhar Kapur",
        "cast": ["Anil Kapoor (Arun / Mr. India)", "Sridevi (Seema)", "Amrish Puri (Mogambo)", "Satish Kaushik (Calendar)", "Baby Aftab (Tina)"],
        "essence_theme": "A selfless caretaker finds an invisibility watch with a secret red-light vulnerability to protect orphans, avenge Tina's tragic murder, and prevent Mogambo's nuclear annihilation of India.",
        "milestones": [
            {
                "id": 1,
                "act": "Act 1: Prologue & The Villain's Ambition",
                "title": "Mogambo's Grand Citadel ('Mogambo Khush Hua')",
                "description": "Amrish Puri as Mogambo in golden embroidered attire on his island fortress declaring his quest to conquer India.",
                "narration_hi": "कहानी की शुरुआत होती है खूंखार खलनायक मोगैंबो से, जो अपने गुप्त टापू के सिंहासन से भारत पर राज करने का ऐलान करता है—मोगैंबो खुश हुआ!",
                "narration_en": "The story opens with the dreaded villain Mogambo, who proclaims his grand conquest of India from his island citadel: Mogambo Khush Hua!",
                "start_sec": 358.0,
                "end_sec": 388.0,
                "duration_sec": 30.0,
                "target_time_sec": 358.0,
                "importance": 100
            },
            {
                "id": 2,
                "act": "Act 1: The Heart & Orphanage",
                "title": "Arun, Calendar & The Orphan Children ('Zindagi Ki Yahi Reet Hai')",
                "description": "Arun in his fedora and brown jacket cares for the orphan children with Calendar, singing of hope and courage despite heavy debt and eviction threats.",
                "narration_hi": "दूसरी तरफ बॉम्बे में, दयालु अरुण और उसका वफादार रसोइया कैलेंडर, बेसहारा अनाथ बच्चों को प्यार से संभाल रहे हैं।",
                "narration_en": "Meanwhile in Bombay, kind-hearted Arun and his loyal cook Calendar lovingly care for a home full of orphaned children.",
                "start_sec": 1040.0,
                "end_sec": 1070.0,
                "duration_sec": 30.0,
                "target_time_sec": 1040.0,
                "importance": 90
            },
            {
                "id": 3,
                "act": "Act 1: Inciting Meeting & Romance",
                "title": "Seema Moves In As Tenant (Humor & Romantic Spark)",
                "description": "Investigative crime journalist Seema arrives with suitcase looking for a room to rent, sparking chaos and laughter with the kids.",
                "narration_hi": "अपराध पत्रकार सीमा किराये का कमरा ढूंढते हुए अरुण के घर आती है, जहां बच्चों की शरारतों और हंसी-मजाक के बीच एक नया रिश्ता जन्म लेता है।",
                "narration_en": "Crime reporter Seema arrives looking for a room to rent, sparking hilarious chaos with the kids and the start of a deep bond with Arun.",
                "start_sec": 2390.0,
                "end_sec": 2418.0,
                "duration_sec": 28.0,
                "target_time_sec": 2390.0,
                "importance": 88
            },
            {
                "id": 4,
                "act": "Act 2A: The Miracle Discovery",
                "title": "Secret Lab & Invisibility Watch (Red Light Rule)",
                "description": "In his late father's hidden laboratory, Arun puts on the miraculous invisibility watch and turns invisible in front of the astonished boy.",
                "narration_hi": "अपने वैज्ञानिक पिता की गुप्त प्रयोगशाला में अरुण को एक चमत्कारी घड़ी मिलती है, जो लाल रोशनी के अलावा इंसान को पूरी तरह अदृश्य कर देती है।",
                "narration_en": "In his late father's secret laboratory, Arun discovers a miraculous watch that turns its wearer invisible—detectable only under red light.",
                "start_sec": 4360.0,
                "end_sec": 4390.0,
                "duration_sec": 30.0,
                "target_time_sec": 4360.0,
                "importance": 98
            },
            {
                "id": 5,
                "act": "Act 2A: Undercover Infiltration",
                "title": "Seema Infiltrates the Casino as 'Hawa Hawai'",
                "description": "Seema performs the sensational Hawa Hawai dance undercover at the casino to investigate Mogambo's smuggling ring led by Daaga and Teja.",
                "narration_hi": "मोगैंबो के तस्कर गिरोह का भंडाफोड़ करने के लिए सीमा कैसीनो में 'हवा-हवाई' बनकर एक धमाकेदार जासूसी मिशन पर उतरती है।",
                "narration_en": "To expose Mogambo's smuggling syndicate, Seema goes undercover at the casino as the dazzling dancer Hawa Hawai.",
                "start_sec": 4680.0,
                "end_sec": 4710.0,
                "duration_sec": 30.0,
                "target_time_sec": 4680.0,
                "importance": 92
            },
            {
                "id": 6,
                "act": "Act 2A: The Invisible Vigilante",
                "title": "Mr. India Thrashes the Smugglers & Feeds the Hungry",
                "description": "The invisible hero delivers righteous slaps to Daaga and the smugglers, throwing them into panic and creating the legendary name of Mr. India.",
                "narration_hi": "अदृश्य होकर अरुण तस्करों और कालाबाजारियों पर कहर बनकर टूटता है, और शहर में इंसाफ के रक्षक 'मिस्टर इंडिया' का नाम गूंज उठता है।",
                "narration_en": "Using invisibility, Arun unleashes furious justice on corrupt smugglers, birthing the legend of the invisible hero: Mr. India.",
                "start_sec": 5290.0,
                "end_sec": 5320.0,
                "duration_sec": 30.0,
                "target_time_sec": 5290.0,
                "importance": 94
            },
            {
                "id": 7,
                "act": "Act 2B: The Magical Romance",
                "title": "The Invisible Romance in the Rain ('Kaate Nahi Kat Te')",
                "description": "Seema in the iconic blue saree dances sensually in the pouring rain with the invisible Mr. India, feeling his touch and presence.",
                "narration_hi": "नीली साड़ी में भीगती सीमा और अदृश्य मिस्टर इंडिया के बीच का यह जादुई अहसास और रोमांस, हिंदी सिनेमा का सबसे अमर पल बन जाता है।",
                "narration_en": "Drenched in a blue saree, Seema and the invisible Mr. India share an ethereal, unforgettable romantic dance in the rain.",
                "start_sec": 7490.0,
                "end_sec": 7522.0,
                "duration_sec": 32.0,
                "target_time_sec": 7490.0,
                "importance": 95
            },
            {
                "id": 8,
                "act": "Act 2B: The Darkest Hour & Tragedy",
                "title": "The Toy Bomb Tragedy & Tina's Heartbreaking Martyrdom",
                "description": "Mogambo plants bombs in innocent toys; sweet little Tina innocently picks up a smiling clown doll, dying in the catastrophic blast.",
                "narration_hi": "लेकिन मोगैंबो की हैवानियत तब सामने आती है जब मासूम नन्हीं टीना पार्क में खिलौना गुड़िया उठाती है और एक भयानक बम धमाके में शहीद हो जाती है।",
                "narration_en": "Tragedy strikes when Mogambo plants bombs in toys; sweet little Tina innocently picks up a doll and is tragically killed in the blast.",
                "start_sec": 8350.0,
                "end_sec": 8382.0,
                "duration_sec": 32.0,
                "target_time_sec": 8350.0,
                "importance": 100
            },
            {
                "id": 9,
                "act": "Act 3: The Threat of Boiling Acid",
                "title": "Kidnapping the Orphans to Mogambo's Fortress",
                "description": "Mogambo's thugs storm the laboratory, capture Calendar and the children, and hang them above boiling acid vats demanding the invisibility watch.",
                "narration_hi": "मोगैंबो के गुंडे अनाथ बच्चों और कैलेंडर को बंधक बनाकर गुप्त टापू पर ले आते हैं, और घड़ी न मिलने पर उन्हें खौलते तेजाब में फेंकने की धमकी देते हैं।",
                "narration_en": "Mogambo abducts Calendar and the children to his volcanic island fortress, threatening to drop them into boiling acid.",
                "start_sec": 9290.0,
                "end_sec": 9320.0,
                "duration_sec": 30.0,
                "target_time_sec": 9290.0,
                "importance": 96
            },
            {
                "id": 10,
                "act": "Act 3: The Climax & Final Victory",
                "title": "Nuclear Missiles Disarmed, Mogambo Slain & Freedom",
                "description": "Arun breaches the control room, halts the nuclear missile countdown, drops Mogambo into the boiling acid, and celebrates freedom with the rescued children.",
                "narration_hi": "अरुण मोगैंबो की न्यूक्लियर मिसाइलों को रोक देता है, मोगैंबो को तेजाब में गिराकर खत्म करता है, और बच्चों को बचाकर आज़ादी की नई सुबह लाता है!",
                "narration_en": "Arun aborts Mogambo's nuclear missile launch, casts the tyrant into his own acid pit, and frees the children to safety under the open sky!",
                "start_sec": 10000.0,
                "end_sec": 10035.0,
                "duration_sec": 35.0,
                "target_time_sec": 10000.0,
                "importance": 100
            }
        ]
    },
    "sholay": {
        "title": "Sholay (1975)",
        "milestones": [
            {"id": 1, "act": "Act 1", "title": "Thakur Recruits Veeru & Jai", "target_percent": 0.10, "importance": 90},
            {"id": 2, "act": "Act 1", "title": "Train Heist & Trust Forged", "target_percent": 0.20, "importance": 85},
            {"id": 3, "act": "Act 2A", "title": "Gabbar Singh Intro ('Kitne Aadmi The')", "target_percent": 0.32, "importance": 100},
            {"id": 4, "act": "Act 2B", "title": "Thakur's Tragic Backstory & Severed Arms", "target_percent": 0.52, "importance": 95},
            {"id": 5, "act": "Act 2B", "title": "Holi Attack in Ramgarh", "target_percent": 0.65, "importance": 88},
            {"id": 6, "act": "Act 3", "title": "Jai's Bridge Sacrifice", "target_percent": 0.82, "importance": 98},
            {"id": 7, "act": "Act 3", "title": "Thakur's Spiked Shoe Vengeance on Gabbar", "target_percent": 0.94, "importance": 95}
        ]
    },
    "inception": {
        "title": "Inception (2010)",
        "milestones": [
            {"id": 1, "act": "Act 1", "title": "Cobb & Dream Extraction Setup", "target_percent": 0.12, "importance": 90},
            {"id": 2, "act": "Act 1", "title": "Saito's Offer & Assembling the Team", "target_percent": 0.25, "importance": 88},
            {"id": 3, "act": "Act 2A", "title": "Entering Fischer's Multi-Layer Dream", "target_percent": 0.40, "importance": 92},
            {"id": 4, "act": "Act 2B", "title": "Zero-G Hotel Hallway Fight", "target_percent": 0.60, "importance": 98},
            {"id": 5, "act": "Act 2B", "title": "Mal's Shadow & The Snow Fortress", "target_percent": 0.75, "importance": 94},
            {"id": 6, "act": "Act 3", "title": "Limbo Descent to Rescue Saito", "target_percent": 0.88, "importance": 96},
            {"id": 7, "act": "Act 3", "title": "The Kick, Awakening & The Spinning Top", "target_percent": 0.96, "importance": 100}
        ]
    }
}


TIMING_NOTES = {
    "exact": "Beat timestamps match the reference copy of this film.",
    "scaled": ("This copy's length differs from the reference copy, so beat timestamps were scaled "
               "proportionally. Check the scene previews; they can be off by a minute or two."),
    "percent": "Beats are placed at estimated positions in the story.",
    "generic": ("This film is not in the essence database and Gemini was not used, so generic three-act "
                "positions are used, not this film's real scenes. Add a Gemini key or a database entry "
                "for a film-specific cut.")
}


def format_seconds_local(seconds: float) -> str:
    s = int(round(max(0.0, seconds)))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h > 0 else f"{m:02d}:{sec:02d}"


def clean_movie_title(raw_name: str) -> str:
    """Extracts a clean film title from a file name or YouTube title."""
    name = str(raw_name or "")
    if "/" in name or "\\" in name:
        name = Path(name).name
    for ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.ts']:
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
            break
    for sep in ('|', '｜'):
        if sep in name:
            name = name.split(sep)[0]
    if ' - ' in name:
        parts = name.split(' - ')
        if len(parts[0].strip()) >= 3:
            name = parts[0]
    tail = re.search(r'(?i)\bfull\s+(movie|film)\b', name)
    if tail and tail.start() > 2:
        name = name[:tail.start()]  # 'Hindi Medium Full Movie: cast...' -> 'Hindi Medium'
    name = re.sub(r'\[.*?\]|\(.*?\)', ' ', name)
    # Language words are only removed when they describe the release ('Hindi Dubbed'), never from titles like 'Hindi Medium'
    name = re.sub(r'(?i)\b(hindi|english|tamil|telugu|malayalam|kannada)\s+(dubbed|version|audio)\b', ' ', name)
    keywords = (r'\b(full movie|full film|movie|full|dubbed|4k|hd|uhd|bluray|remux|webrip|'
                r'web-dl|brrip|dts|aac|hevc|x264|x265|dvdrip|\d{3,4}p)\b')
    for _ in range(3):
        name = re.sub(keywords, ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'[_\.\-\+]+', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()


def _name_pattern(name: str) -> Optional["re.Pattern"]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    if not words:
        return None
    return re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]*".join(map(re.escape, words)) + r"(?![a-z0-9])")


def match_database_entry(film_title: str):
    """
    Finds a database film whose full name (or alias) appears as whole words in the cleaned title.
    Empty or partial titles never match (the old substring check matched 'Movie.mp4' to Mr. India).
    """
    cleaned = clean_movie_title(film_title).lower()
    if not re.search(r"[a-z0-9]", cleaned):
        return None
    best = None
    for key, data in OFFLINE_ESSENCE_DATABASE.items():
        for name in [key, data.get("title", "")] + list(data.get("aliases", [])):
            base = re.sub(r"\(\d{4}\)", "", name).strip()
            pattern = _name_pattern(base)
            if pattern and pattern.search(cleaned):
                if best is None or len(base) > best[2]:
                    best = (key, data, len(base))
    return (best[0], best[1]) if best else None


def _database_milestones(data: Dict[str, Any], total: float):
    ref = data.get("reference_duration_sec")
    has_abs = any("start_sec" in m for m in data["milestones"])
    timing, ratio = "percent", 1.0
    if has_abs:
        if ref and abs(total - ref) > max(90.0, 0.02 * ref):
            timing, ratio = "scaled", total / ref
        else:
            timing = "exact"
    out = []
    for m in data["milestones"]:
        item = dict(m)
        if "start_sec" in item:
            span = float(item.get("end_sec", float(item["start_sec"]) + 30.0)) - float(item["start_sec"])
            start = float(item["start_sec"]) * ratio
            item["start_sec"] = round(start, 2)
            item["end_sec"] = round(start + span, 2)
            item["target_time_sec"] = item["start_sec"]
            item["exact"] = timing == "exact"
        else:
            item["target_time_sec"] = round(total * float(item.get("target_percent", 0.5)), 1)
        item["target_percent"] = round(item["target_time_sec"] / max(1.0, total), 4)
        item["target_time_formatted"] = format_seconds_local(item["target_time_sec"])
        if item["target_time_sec"] < total - 5.0:
            out.append(item)
    return out, timing


def extract_film_canonical_essence(film_title: str, total_duration_sec: float,
                                   gemini_api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns the film's story blueprint: its key milestones, where they sit in this copy, and how reliable
    that timing is. Sources, in order: built-in database, Gemini, generic three-act structure.
    """
    cleaned = clean_movie_title(film_title)

    match = match_database_entry(film_title)
    if match:
        key, data = match
        milestones, timing = _database_milestones(data, total_duration_sec)
        return {
            "source": "canonical_knowledge_database",
            "movie_title": data.get("title", cleaned),
            "director": data.get("director", "Unknown"),
            "cast": data.get("cast", []),
            "essence_theme": data.get("essence_theme", "Core narrative arc"),
            "milestones": milestones,
            "timing": timing,
            "timing_note": TIMING_NOTES[timing],
            "is_generic": False
        }

    from backend.video_engine import llm_client
    if cleaned and llm_client.provider_for(gemini_api_key):
        try:
            def generate_json(key, prompt, temperature=0.2, timeout=60):
                # Any free provider (Gemini keys and models, Groq, Cloudflare, NVIDIA, then the local model)
                return llm_client.generate_json(prompt, api_key=key, temperature=temperature, timeout=max(timeout, 120))
            prompt = f"""You are a film dramaturge. For the feature film "{cleaned}", list the 7 to 10 story milestones that
define its plot, in chronological order: setup and premise, inciting incident, antagonist or threat, key subplot,
midpoint turn, darkest hour, crisis, climax and resolution. Only use events that really happen in this film.
If you do not know this film, return {{"known": false, "milestones": []}}.

For each milestone give target_percent (0.0-1.0, where in the runtime it happens), importance (0-100), a factual
one-line description, and a short original narration line in English (narration_en) and Hindi (narration_hi).

JSON format:
{{"known": true, "movie_title": "{cleaned}", "essence_theme": "one sentence", "milestones": [
  {{"act": "Act 1: Setup", "title": "Short scene title", "description": "What happens", "target_percent": 0.08,
    "importance": 90, "narration_en": "...", "narration_hi": "..."}}]}}"""
            parsed = generate_json(gemini_api_key, prompt, temperature=0.2, timeout=60)
            milestones = []
            for m in parsed.get("milestones", []) if parsed.get("known", True) else []:
                try:
                    pct = float(m.get("target_percent"))
                except (TypeError, ValueError):
                    continue
                if pct > 1.0:
                    pct /= 100.0
                pct = min(0.98, max(0.01, pct))
                item = {
                    "act": str(m.get("act", "Story Beat")),
                    "title": str(m.get("title", "Story beat")),
                    "description": str(m.get("description", "")),
                    "target_percent": round(pct, 4),
                    "importance": int(m.get("importance", 90)) if str(m.get("importance", "90")).isdigit() else 90,
                    "narration_en": m.get("narration_en"),
                    "narration_hi": m.get("narration_hi"),
                    "target_time_sec": round(total_duration_sec * pct, 1)
                }
                item["target_time_formatted"] = format_seconds_local(item["target_time_sec"])
                milestones.append(item)
            milestones.sort(key=lambda x: x["target_percent"])
            for i, m in enumerate(milestones, 1):
                m["id"] = i
            if len(milestones) >= 3:
                return {
                    "source": "gemini_cinema_director",
                    "movie_title": parsed.get("movie_title", cleaned),
                    "essence_theme": parsed.get("essence_theme", ""),
                    "milestones": milestones,
                    "timing": "percent",
                    "timing_note": TIMING_NOTES["percent"],
                    "is_generic": False
                }
        except Exception as e:
            print(f"Gemini essence extraction failed, using the generic blueprint: {e}")

    archetypal = [
        {"id": 1, "act": "Act 1: Setup", "title": "World & Character Exposition", "description": "Introduction of the protagonist and their world.", "target_percent": 0.08, "importance": 85},
        {"id": 2, "act": "Act 1: Inciting Incident", "title": "The Catalyst", "description": "An event disrupts the protagonist's world.", "target_percent": 0.20, "importance": 95},
        {"id": 3, "act": "Act 2A: Rising Action", "title": "Entering the New World", "description": "The threat emerges and the stakes rise.", "target_percent": 0.35, "importance": 90},
        {"id": 4, "act": "Act 2B: Midpoint Reversal", "title": "The Midpoint Turn", "description": "A revelation shifts the story's direction.", "target_percent": 0.50, "importance": 96},
        {"id": 5, "act": "Act 2B: The Darkest Hour", "title": "The Emotional Crisis", "description": "The lowest point, where defeat looks certain.", "target_percent": 0.68, "importance": 98},
        {"id": 6, "act": "Act 3: Mobilization", "title": "The Final Preparation", "description": "The protagonist resolves to confront the crisis.", "target_percent": 0.82, "importance": 92},
        {"id": 7, "act": "Act 3: The Climax", "title": "Final Showdown & Resolution", "description": "The confrontation that resolves the central conflict.", "target_percent": 0.94, "importance": 100}
    ]
    for m in archetypal:
        m["target_time_sec"] = round(total_duration_sec * m["target_percent"], 1)
        m["target_time_formatted"] = format_seconds_local(m["target_time_sec"])
    return {
        "source": "archetypal_narrative_blueprint",
        "movie_title": cleaned or "Untitled",
        "essence_theme": "Classic three-act structure (generic; not specific to this film)",
        "milestones": archetypal,
        "timing": "generic",
        "timing_note": TIMING_NOTES["generic"],
        "is_generic": True
    }
