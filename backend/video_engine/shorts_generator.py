import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from backend.config import FFMPEG_BIN, TEMP_DIR, AUDIO_SAMPLE_RATE
from backend.video_engine.subtitles_and_chapters import format_srt_timestamp

BLURRED_WINGS = (
    "split=2[main][bg];"
    "[bg]scale=128:228:force_original_aspect_ratio=increase,crop=128:228,boxblur=4:4,scale=1080:1920[blurred];"
    "[main]scale=1080:-2:force_original_aspect_ratio=decrease[scaled];"
    "[blurred][scaled]overlay=(W-w)/2:(H-h)/2"
)
CAPTION_STYLE = ("FontName=Arial,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                 "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=80")


def _pick_hooks(scenes: List[Dict[str, Any]], count: int, max_duration_sec: float, energy=None,
                shot_cuts=None, video_duration: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    Hook detection: for each selected scene, find its loudest window (audio peak = punchline, explosion,
    musical hit) and score it by story importance + loudness rank + editing pace. Picks the best
    non-overlapping windows.
    """
    from backend.video_engine.audio_analyzer import EnergyIndex
    from backend.video_engine.scene_detector import cut_density_score

    index = EnergyIndex(energy or [])
    picks = []
    for s in scenes:
        if not s.get("selected", True) or s["end"] - s["start"] < 8.0:
            continue
        length = min(max_duration_sec, s["end"] - s["start"])
        start = index.loudest_window(s["start"], s["end"], length) if energy else s["start"]
        start = max(s["start"], min(start, s["end"] - length))
        loud = index.percentile(start, start + length)
        pace = cut_density_score(shot_cuts or [], start, start + length, video_duration or 0.0)
        score = 0.40 * float(s.get("importance", 60)) + 0.35 * loud + 0.25 * pace
        picks.append({"scene": s, "start": start, "duration": length, "score": round(score, 1),
                      "loudness_rank": loud, "pace": pace})
    picks.sort(key=lambda p: -p["score"])
    chosen: List[Dict[str, Any]] = []
    for p in picks:
        if any(p["start"] < c["start"] + c["duration"] and c["start"] < p["start"] + p["duration"] for c in chosen):
            continue
        chosen.append(p)
        if len(chosen) >= count:
            break
    return chosen


def _snap_hook(start: float, dur: float, max_dur: float, inside, clean: List[float]) -> Tuple[float, float]:
    """Moves a short's start and end off the middle of spoken lines, to the nearest line breaks."""
    from bisect import bisect_left, bisect_right
    if clean and inside(start):
        i = bisect_right(clean, start)
        before = clean[i - 1] if i > 0 and start - clean[i - 1] <= 5.0 else None
        after = clean[i] if i < len(clean) and clean[i] - start <= 3.0 else None
        if before is not None:
            dur, start = dur + (start - before), max(0.0, before - 0.05)
        elif after is not None:
            dur, start = dur - (after - start), after
    dur = min(dur, max_dur)
    end = start + dur
    if clean and inside(end):
        lo = bisect_left(clean, end - 8.0)
        hi = bisect_right(clean, min(end + 2.0, start + max_dur))
        options = [b for b in clean[lo:hi] if b - start >= 10.0]
        if options:
            end = min(options, key=lambda b: (b > end, abs(b - end))) + 0.15
    return start, max(5.0, end - start)


def _write_short_ass(subtitles: List[Dict[str, Any]], words: Optional[List[Dict[str, Any]]], start: float, duration: float,
                     path: Path, style: str) -> bool:
    """Animated captions (karaoke / pop / plain) for one reel, with word timings when the transcript has them."""
    from backend.video_engine.flavor_detector import ANY_TAG
    from backend.video_engine.captions import build_ass
    cues = []
    for sub in subtitles or []:
        if sub["end"] <= start or sub["start"] >= start + duration:
            continue
        text = " ".join(ANY_TAG.sub(" ", sub.get("text", "")).split())
        if sum(ch.isalnum() for ch in text) < 2:
            continue
        a, b = max(0.0, sub["start"] - start), min(duration, sub["end"] - start)
        if b - a >= 0.2:
            cues.append({"start": a, "end": b, "text": text})
    if not cues:
        return False
    rel = [{"word": w["word"], "start": w["start"] - start, "end": w["end"] - start}
           for w in (words or []) if start <= w["start"] <= start + duration]
    path.write_text(build_ass(cues, rel or None, style), encoding="utf-8")
    return True


def _write_short_srt(subtitles: List[Dict[str, Any]], start: float, duration: float, path: Path) -> bool:
    lines, idx = [], 1
    from backend.video_engine.flavor_detector import ANY_TAG
    for sub in subtitles or []:
        if sub["end"] <= start or sub["start"] >= start + duration:
            continue
        text = " ".join(ANY_TAG.sub(" ", sub.get("text", "")).split())   # no [Music] / [Applause] tags on screen
        if sum(ch.isalnum() for ch in text) < 2:   # empty, or a lone caption fragment
            continue
        a = max(0.0, sub["start"] - start)
        b = min(duration, sub["end"] - start)
        if b - a < 0.2:
            continue
        lines += [str(idx), f"{format_srt_timestamp(a)} --> {format_srt_timestamp(b)}", text, ""]
        idx += 1
    if idx == 1:
        return False
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


def extract_viral_shorts(
    source_video_path: str,
    selected_scenes: List[Dict[str, Any]],
    job_id: str,
    count: int = 5,
    max_duration_sec: float = 45.0,
    pan_and_scan: bool = False,
    subtitles: Optional[List[Dict[str, Any]]] = None,
    burn_captions: bool = False,
    energy: Optional[List[float]] = None,
    shot_cuts: Optional[List[float]] = None,
    video_duration: Optional[float] = None,
    content_mode: str = "movie",
    caption_style: str = "karaoke",
    words: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Exports up to `count` vertical 9:16 (1080x1920) shorts from the strongest hook moments."""
    if content_mode == "lecture":
        pan_and_scan = False   # keep the board or slides fully visible
    shorts_dir = TEMP_DIR / job_id / "viral_shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    for old in list(shorts_dir.glob("short_*")) + list(shorts_dir.glob("*.zip")):
        try:
            old.unlink()
        except OSError:
            pass

    hooks = _pick_hooks(selected_scenes, count, max_duration_sec, energy, shot_cuts, video_duration)
    if not hooks:
        return []
    if subtitles:
        from backend.video_engine.summarizer import speech_index
        from backend.video_engine.flavor_detector import ANY_TAG
        inside, clean = speech_index([(x["start"], x["end"]) for x in subtitles if ANY_TAG.sub(" ", x.get("text", "")).strip()])
        for hk in hooks:
            hk["start"], hk["duration"] = _snap_hook(hk["start"], hk["duration"], max_duration_sec, inside, clean)

    from backend.video_engine.probe import check_nvenc_support
    v_codec = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "22", "-b:v", "0"]
               if check_nvenc_support() else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"])

    def make(item):
        idx, hook = item
        scene, start, dur = hook["scene"], hook["start"], hook["duration"]
        out_short = shorts_dir / f"short_{idx:02d}.mp4"
        if pan_and_scan:
            from backend.video_engine.face_tracker import compute_dynamic_crop_offset
            _, vf = compute_dynamic_crop_offset(str(source_video_path), start, dur, fallback_filter=BLURRED_WINGS)
        else:
            vf = BLURRED_WINGS
        layout = "full frame" if vf == BLURRED_WINGS else "face tracked"
        srt_name = f"short_{idx:02d}.srt"
        ass_name = f"short_{idx:02d}.ass"
        captioned = False
        if burn_captions and caption_style in ("karaoke", "pop", "plain"):
            if _write_short_ass(subtitles or [], words, start, dur, shorts_dir / ass_name, caption_style):
                vf = f"{vf},subtitles={ass_name}"
                captioned = True
        elif burn_captions and _write_short_srt(subtitles or [], start, dur, shorts_dir / srt_name):
            vf = f"{vf},subtitles={srt_name}:force_style='{CAPTION_STYLE}'"
            captioned = True
        vf += ",format=yuv420p"
        cmd = [FFMPEG_BIN, "-y", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(Path(source_video_path).resolve()),
               "-vf", vf, *v_codec, "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
               "-movflags", "+faststart", out_short.name]
        try:
            # cwd=shorts_dir lets the subtitles filter use a relative path (avoids Windows drive-letter escaping)
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding="utf-8", errors="replace", timeout=300, cwd=str(shorts_dir))
            if res.returncode != 0 or not out_short.exists():
                print(f"Short {idx} failed: {res.stderr.strip().splitlines()[-1:]}")
                return None
        except Exception as e:
            print(f"Error generating short {idx}: {e}")
            return None
        return {
            "short_id": idx,
            "file_name": out_short.name,
            "title": f"Short #{idx}: {scene.get('title', 'Highlight')}",
            "act": scene.get("act", "Highlights"),
            "start": round(start, 2),
            "duration": round(dur, 2),
            "hook_score": hook["score"],
            "layout": layout,
            "captions": captioned,
            "caption_style": caption_style if captioned else None,
            "file_path": str(out_short),
            "stream_url": f"/api/shorts/{job_id}/{idx}",
            "download_url": f"/api/shorts/{job_id}/{idx}?download=true"
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(make, enumerate(hooks, start=1)))
    return [r for r in results if r]


def bundle_shorts_into_zip(job_id: str) -> Optional[str]:
    """Compresses the generated shorts into a ZIP pack (rebuilt every time)."""
    shorts_dir = TEMP_DIR / job_id / "viral_shorts"
    if not shorts_dir.exists():
        return None
    files = sorted(shorts_dir.glob("short_*.mp4"))
    if not files:
        return None
    zip_path = shorts_dir / f"CineCut_Shorts_{job_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    return str(zip_path)
