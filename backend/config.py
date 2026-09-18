import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Loads KEY=VALUE lines from cine_cut/.env into the environment. Empty values are ignored and
    variables already set in Windows take priority. An AI key name listed twice with a different value
    (e.g. two GEMINI_API_KEY lines) is kept as an extra numbered key, so no key is lost."""
    if not path.exists():
        return
    import re as _re
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        m = _re.match(r"^([A-Z]+_API_KEY)(?:_(\d+))?$", key)
        if key in os.environ and os.environ[key] != value and m:
            if value in os.environ.values():
                continue
            n = 2
            while f"{m.group(1)}_{n}" in os.environ or f"{m.group(1)}_{n}" in _NAMES_IN_FILE:
                n += 1
            os.environ[f"{m.group(1)}_{n}"] = value
        elif key not in os.environ:
            os.environ[key] = value


def _names(path: Path) -> set:
    try:
        return {l.split("=", 1)[0].strip() for l in path.read_text(encoding="utf-8-sig").splitlines() if "=" in l and not l.strip().startswith("#")}
    except OSError:
        return set()


_NAMES_IN_FILE = _names(Path(__file__).resolve().parent.parent / ".env")


_load_env_file(BASE_DIR / ".env")

TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"
FRONTEND_DIR = BASE_DIR / "frontend"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# FFmpeg executables (override with env vars if FFmpeg is not on PATH)
FFMPEG_BIN = os.environ.get("CINECUT_FFMPEG", "ffmpeg")
FFPROBE_BIN = os.environ.get("CINECUT_FFPROBE", "ffprobe")

# Server (run.py sets CINECUT_PORT / CINECUT_LAN before starting uvicorn)
SERVER_PORT = int(os.environ.get("CINECUT_PORT", "8080"))
LAN_MODE = os.environ.get("CINECUT_LAN", "0") == "1"

# Gemini (override the model id with CINECUT_GEMINI_MODEL)
DEFAULT_GEMINI_MODEL = os.environ.get("CINECUT_GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Summarization defaults
DEFAULT_TARGET_MINUTES = 30
DEFAULT_MIN_SCENE_SEC = 15.0
DEFAULT_MAX_SCENE_SEC = 90.0
PADDING_HEAD_SEC = 0.2
PADDING_TAIL_SEC = 0.3
AUDIO_CROSSFADE_SEC = 0.25

# Voiceover mixing
VOICEOVER_LEAD_SEC = 0.7      # narrator starts this long after the cut
VOICEOVER_DUCK_LEVEL = 0.30   # movie audio level while the narrator speaks
VOICEOVER_GAIN = 1.3          # narrator gain before the limiter
VOICEOVER_TAIL_SEC = 0.8      # breathing room after the narration ends

# Rendering
RENDER_WORKERS = max(1, int(os.environ.get("CINECUT_RENDER_WORKERS", "3")))
AUDIO_SAMPLE_RATE = 48000

# Presets and flavor profiles
PRESETS = {
    "story_focused": {
        "name": "Story & Plot Cut",
        "description": "Prioritizes narrative revelations, character conversations, and story turns.",
        "weights": {"dialogue": 0.55, "audio_dynamic": 0.20, "motion": 0.15, "coverage": 0.10}
    },
    "action_energy": {
        "name": "Action & Climax Highlights",
        "description": "Emphasizes intense action, high audio energy, soundtrack swells, and fast visual motion.",
        "weights": {"dialogue": 0.15, "audio_dynamic": 0.50, "motion": 0.25, "coverage": 0.10}
    },
    "musical_romance": {
        "name": "Musical & Romance Setpieces",
        "description": "Preserves song-and-dance numbers, romantic chemistry, and melodic soundtrack highlights.",
        "weights": {"dialogue": 0.30, "audio_dynamic": 0.40, "motion": 0.15, "coverage": 0.15}
    },
    "comedy_fun": {
        "name": "Comedy & Humorous Banter",
        "description": "Prioritizes comedic bickering, sharp back-and-forth dialogue, and lighthearted interactions.",
        "weights": {"dialogue": 0.60, "audio_dynamic": 0.15, "motion": 0.15, "coverage": 0.10}
    },
    "emotional_drama": {
        "name": "Emotional & Tragic Drama",
        "description": "Focuses on turning points, emotional sacrifices, and the darkest hour.",
        "weights": {"dialogue": 0.50, "audio_dynamic": 0.30, "motion": 0.10, "coverage": 0.10}
    },
    "hero_spotlight": {
        "name": "Hero Spotlight Arc",
        "description": "Biases selection around the protagonist and their triumphs.",
        "weights": {"dialogue": 0.40, "audio_dynamic": 0.30, "motion": 0.20, "coverage": 0.10}
    },
    "villain_lore": {
        "name": "Villain Lore & Scheming",
        "description": "Focuses on the antagonist, their speeches, and their schemes.",
        "weights": {"dialogue": 0.45, "audio_dynamic": 0.35, "motion": 0.10, "coverage": 0.10}
    },
    "balanced_cinema": {
        "name": "Balanced Cinema Cut",
        "description": "Distributes time across the story arc while keeping high-impact dialogue and action.",
        "weights": {"dialogue": 0.40, "audio_dynamic": 0.30, "motion": 0.15, "coverage": 0.15}
    },
    "lecture_study": {
        "name": "Lecture Study Cut",
        "description": "Keeps definitions, key explanations and summaries; trims pauses, tangents and housekeeping.",
        "weights": {"dialogue": 0.80, "audio_dynamic": 0.05, "motion": 0.15, "coverage": 0.00}
    }
}
