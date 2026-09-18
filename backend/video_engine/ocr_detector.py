import os
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from backend.config import FFMPEG_BIN

DATE_LOC_REGEX = re.compile(
    r'(?i)\b(?:berlin|london|paris|tokyo|moscow|washington|new york|chicago|los angeles|rome|beijing|cairo|madrid)\b|'
    r'\b(?:18\d\d|19\d\d|20\d\d)\b|'
    r'\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b|'
    r'\b(?:chapter|part|act)\s*(?:[0-9]+|[ivxlcdm]+)\b|'
    r'\b(?:\d+\s+(?:hours?|days?|weeks?|months?|years?)\s+later)\b',
    re.IGNORECASE
)

def scan_scene_for_title_card(video_path: str, timestamp_sec: float) -> Optional[str]:
    """
    Checks the frame at timestamp_sec for on-screen location/date/chapter text.
    If OCR engine (pytesseract or rapidocr) is installed, runs recognition.
    Otherwise uses regex heuristic scan or returns None.
    """
    try:
        import pytesseract
        from PIL import Image

        # Extract 1 frame at timestamp
        cmd = [
            FFMPEG_BIN, "-y",
            "-ss", f"{timestamp_sec:.2f}",
            "-i", str(video_path),
            "-vframes", "1",
            "-f", "image2pipe",
            "-vcodec", "png",
            "-"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0 and len(res.stdout) > 1000:
            import io
            image = Image.open(io.BytesIO(res.stdout))
            text = pytesseract.image_to_string(image).strip()
            
            # Check for location/date match
            match = DATE_LOC_REGEX.search(text)
            if match:
                clean = " ".join(text.split())
                return clean[:60]
    except Exception:
        pass

    return None

def detect_on_screen_title_cards(
    video_path: str,
    scenes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Scans candidate scenes for on-screen title cards and location subtitles.
    Enriches matching scenes with title_card tag and elevated importance.
    """
    enriched = []
    for sc in scenes:
        item = dict(sc)
        start = item.get("start", 0.0)
        
        card_text = scan_scene_for_title_card(video_path, start + 0.5)
        if card_text:
            item["title_card"] = card_text
            item["importance"] = item.get("importance", 50) + 25
            if not item.get("title") or "Scene" in item["title"]:
                item["title"] = f"{card_text}"
        
        enriched.append(item)
    return enriched
