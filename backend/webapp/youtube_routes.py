"""The YouTube pack page (/youtube) and its studio API. Studio routes: served to this PC only, like the rest of the studio."""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from backend.webapp import youtube, yt_voice

router = APIRouter()
PAGE = Path(__file__).resolve().parents[2] / "frontend" / "youtube.html"
FILES = {"video.mp4": "video/mp4", "short.mp4": "video/mp4", "thumb.jpg": "image/jpeg", "transcript.txt": "text/plain; charset=utf-8"}


class MakeRequest(BaseModel):
    series: str
    count: int = Field(3, ge=1, le=20)
    language: str = "both"          # hi, en or both
    voice_en: str = "en-gb"         # en-gb or en-us
    voice_hi: str = "hi"            # hi or hi-f


class StatusRequest(BaseModel):
    status: str
    url: Optional[str] = None


@router.get("/youtube")
def youtube_page():
    return FileResponse(str(PAGE), media_type="text/html")


@router.get("/api/studio/youtube/queue")
def youtube_queue():
    ready = {s: {c: len(youtube.candidates(s, 500, c)) for c in ("hi", "en")} for s in ("classics", "vedas", "audio", "lectures")}
    return {"packs": youtube.queue(), "state": youtube.STATE, "series": youtube.SERIES, "series_en": youtube.SERIES_EN, "ready": ready,
            "voices": {k: v["label"] for k, v in yt_voice.PRESETS.items()}}


@router.post("/api/studio/youtube/make")
def youtube_make(req: MakeRequest):
    try:
        return youtube.start_batch(req.series, req.count, req.language, {"en": req.voice_en, "hi": req.voice_hi})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/studio/youtube/{pid}/status")
def youtube_status(pid: str, req: StatusRequest):
    try:
        return youtube.set_status(pid, req.status, req.url)
    except (KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="No such pack.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/studio/youtube/{pid}/metadata.txt")
def youtube_metadata(pid: str):
    try:
        pack = next(p for p in youtube.queue() if p["id"] == pid)
    except StopIteration:
        raise HTTPException(status_code=404, detail="No such pack.")
    return PlainTextResponse(youtube.metadata_text(pack), headers={"Content-Disposition": f'attachment; filename="{pid}.txt"'})


@router.get("/api/studio/youtube/{pid}/{name}")
def youtube_file(pid: str, name: str, download: bool = False):
    if name not in FILES:
        raise HTTPException(status_code=404, detail="No such file.")
    try:
        path = youtube.pack_dir(pid) / name
    except KeyError:
        raise HTTPException(status_code=404, detail="No such pack.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="No such file.")
    return FileResponse(str(path), media_type=FILES[name], filename=f"{pid}_{name}" if download else None)
