import json
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from backend.video_engine.probe import format_seconds

TRENDING_HASHTAGS = ["#movierecap", "#filmreview", "#cinema", "#moviereview", "#shorts"]

HOOK_TEMPLATES = [
    "The scene that changes everything in {movie}",
    "Why this moment in {movie} still works",
    "The turning point of {movie}, explained",
    "What most people miss in this scene",
    "The moment {movie} shows its hand"
]

VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts")


def _strip_video_ext(name: str) -> str:
    name = Path(str(name or "")).name
    return name[: -len(Path(name).suffix)] if Path(name).suffix.lower() in VIDEO_SUFFIXES else name


CREDIT_LINE = ("Footage from \"{movie}\" is used for commentary and review. All rights belong to the original "
               "rights holders.")


def _clean_title(movie_title: str) -> str:
    return _strip_video_ext(movie_title).replace("_", " ").strip() if movie_title else "this film"


def generate_viral_seo_metadata(short_idx: int, short_title: str, movie_title: str, act_name: str = "Climax") -> Dict[str, Any]:
    """Title, description and tags for one vertical short, with a credit line."""
    clean_movie = _clean_title(movie_title)
    hook = HOOK_TEMPLATES[(short_idx - 1) % len(HOOK_TEMPLATES)].format(movie=clean_movie)
    act_label = act_name.split(":")[0] if isinstance(act_name, str) else "Highlight"
    viral_title = f"{hook} | {clean_movie} ({act_label})"
    description = (f"{hook}\n\nFilm: {clean_movie}\nScene #{short_idx} ({act_label})\n\n"
                   f"{CREDIT_LINE.format(movie=clean_movie)}\n\n{' '.join(TRENDING_HASHTAGS)}")
    return {
        "short_index": short_idx,
        "movie_title": clean_movie,
        "viral_title": viral_title,
        "description": description,
        "hashtags": TRENDING_HASHTAGS,
        "copy_paste_text": f"TITLE: {viral_title}\n\nDESCRIPTION:\n{description}"
    }


def generate_social_publishing_pack(shorts_list: List[Dict[str, Any]], movie_title: str) -> List[Dict[str, Any]]:
    pack = []
    for s in shorts_list:
        idx = s.get("short_id", 1)
        meta = generate_viral_seo_metadata(idx, s.get("title", f"Short #{idx}"), movie_title, s.get("act", "Story Peak"))
        meta["file_name"] = s.get("file_name", f"short_{idx:02d}.mp4")
        meta["download_url"] = s.get("download_url", "")
        pack.append(meta)
    return pack


def youtube_chapter_lines(chapters: List[Dict[str, Any]]) -> List[str]:
    """
    YouTube chapter rules: first timestamp 00:00, at least 3 chapters, each at least 10 seconds.
    Chapters shorter than 10 s are merged into the previous one.
    """
    merged: List[Dict[str, Any]] = []
    for ch in chapters:
        if merged and (ch["end"] - ch["start"] < 10.0 or ch["start"] - merged[-1]["start"] < 10.0):
            merged[-1]["end"] = ch["end"]
            continue
        merged.append(dict(ch))
    if not merged:
        return []
    merged[0]["start"] = 0.0
    if len(merged) < 3:
        return []
    return [f"{format_seconds(ch['start'])} {ch['title']}" for ch in merged]


def generate_youtube_upload_pack(movie_title: str, chapters: List[Dict[str, Any]], language: str = "English",
                                 ai_voice: bool = True, content_mode: str = "movie",
                                 total_duration: Optional[float] = None) -> Dict[str, Any]:
    """Everything needed for a manual YouTube Studio upload of the rendered cut."""
    clean = _clean_title(movie_title)
    lines = youtube_chapter_lines(chapters)
    if content_mode == "lecture":
        title = f"{clean} | Study Cut with Chapters"
        intro = f"A condensed study cut of \"{clean}\" keeping the key explanations, with chapters for revision."
        tags = ["lecture", "study", "notes", "revision", clean]
    else:
        title = f"{clean} | Story Recap and Review"
        intro = f"My recap and commentary on \"{clean}\"."
        tags = ["movie recap", "film review", "movie explained", clean]
    parts = [intro, ""]
    if lines:
        parts += ["Chapters:"] + lines + [""]
    parts.append(CREDIT_LINE.format(movie=clean))
    if ai_voice:
        parts.append("Narration uses a synthetic (text-to-speech) voice.")
    checklist = [
        "Upload manually in YouTube Studio. API uploads from unaudited Google Cloud projects are forced to Private.",
        "In 'Altered or synthetic content', answer Yes only if the video shows realistic people or events that did not "
        "happen. A plain text-to-speech narrator does not need it on its own; imitating a real person's voice does.",
        "Expect Content ID claims on copyrighted footage. A claim can block the video or send its ad revenue to the "
        "rights holder. Three copyright strikes within 90 days terminate the channel.",
        "Monetization review looks for original commentary. Template-style narrated recaps are treated as reused or "
        "inauthentic content.",
        "Edge TTS voices are for personal use. For a monetized channel, record your own voice or configure Azure AI Speech."
    ]
    return {
        "title": title[:100],
        "description": "\n".join(parts),
        "tags": tags,
        "chapters_included": bool(lines),
        "chapter_count": len(lines),
        "category": "Education" if content_mode == "lecture" else "Film & Animation",
        "language": language,
        "duration_formatted": format_seconds(total_duration) if total_duration else None,
        "checklist": checklist
    }


def dispatch_publishing_webhook(webhook_url: str, payload: Dict[str, Any]) -> bool:
    """Posts the publishing payload to the user's own automation webhook (http/https only)."""
    parsed = urlparse(webhook_url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    try:
        req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "User-Agent": "CineCut/6.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.getcode() < 300
    except Exception:
        return False
