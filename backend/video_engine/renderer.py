import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional, Tuple

from backend.config import (FFMPEG_BIN, OUTPUT_DIR, TEMP_DIR, RENDER_WORKERS, AUDIO_SAMPLE_RATE,
                            VOICEOVER_LEAD_SEC, VOICEOVER_TAIL_SEC)
from backend.video_engine.probe import check_nvenc_support, get_media_duration, get_gpu_name, get_video_metadata
from backend.video_engine.audio_smoothing import build_transition_filters
from backend.video_engine.subtitles_and_chapters import create_ffmetadata_chapters, embed_chapters_into_video

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2"]
CACHE_VERSION = "v5"


class RenderError(RuntimeError):
    pass


def sanitize_output_filename(name: Optional[str], default: str) -> str:
    """Keeps only a safe file name (no folders, no Windows-reserved characters)."""
    raw = Path(str(name or "")).name
    raw = _UNSAFE.sub("_", raw).strip(" .")
    if not raw:
        raw = _UNSAFE.sub("_", default)
    if not raw.lower().endswith((".mp4", ".mkv", ".mov")):
        raw += ".mp4"
    return raw[:180]


def _run(cmd: List[str], timeout: Optional[float] = None, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)


def _tail(text: str, n: int = 4) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return " | ".join(lines[-n:])[-600:]


def _file_sig(path: Path) -> str:
    st = path.stat()
    return f"{path}|{st.st_size}|{int(st.st_mtime)}"


def video_codec_args(use_nvenc: bool) -> List[str]:
    if use_nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def fit_voiceovers(active: List[Dict[str, Any]], source_duration: float) -> None:
    """
    Makes sure each narration fits its clip: extends the clip into the following footage when possible
    (never past the next selected scene), otherwise speeds the narration up slightly (max 1.35x).
    """
    for idx, s in enumerate(active):
        s.pop("_vo_tempo", None)
        vo = s.get("voiceover_audio_path")
        if not vo or not os.path.exists(vo):
            continue
        vo_dur = get_media_duration(vo) or 0.0
        need = VOICEOVER_LEAD_SEC + vo_dur + VOICEOVER_TAIL_SEC
        if need > s["end"] - s["start"]:
            limit = source_duration
            if idx + 1 < len(active):
                limit = min(limit, active[idx + 1]["start"])
            new_end = min(limit, s["start"] + need)
            if new_end > s["end"]:
                s["end"] = round(new_end, 2)
                s["duration"] = round(s["end"] - s["start"], 2)
                s["extended_for_voiceover"] = True
        available = (s["end"] - s["start"]) - VOICEOVER_LEAD_SEC - VOICEOVER_TAIL_SEC
        if available > 0.5 and vo_dur > available:
            s["_vo_tempo"] = round(min(1.35, vo_dur / available), 3)


def place_voiceovers(active: List[Dict[str, Any]], speech_cues: Optional[List[Tuple[float, float]]]) -> Dict[str, int]:
    """
    Chooses where each narration is spoken so it does not talk over dialogue, in this order:
      1. a natural pause in the first 12 seconds of its clip;
      2. speech-free footage just before the clip (a natural lead-in: the clip starts a little earlier);
      3. a short still of the clip's first frame (at most one line in five);
      4. otherwise the classic place at the clip start, with the film ducked under the voice.
    Lead-ins and stills add time, so clip endings are then trimmed back on line breaks to keep the cut's length.
    """
    counts = {"pause": 0, "lead_in": 0, "still": 0, "over_film": 0}
    if not speech_cues:
        return counts
    from backend.video_engine.summarizer import speech_index
    cues = sorted(speech_cues)
    voiced = [s for s in active if s.get("voiceover_audio_path") and os.path.exists(s["voiceover_audio_path"])]
    max_holds = max(1, len(voiced) // 5)
    original = sum(s["end"] - s["start"] for s in active)
    added = 0.0
    for idx, s in enumerate(active):
        vo = s.get("voiceover_audio_path")
        if not vo or not os.path.exists(vo):
            continue
        vo_dur = get_media_duration(vo) or 0.0
        need = vo_dur / max(1.0, float(s.get("_vo_tempo", 1.0))) + 0.35
        start, end = s["start"], s["end"]
        horizon = min(end - 0.3, start + 25.0)
        merged: List[Tuple[float, float]] = []
        for a, b in cues:
            if b <= start or a >= horizon:
                continue
            a, b = max(a, start), min(b, end)
            if merged and a <= merged[-1][1] + 0.25:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        free_from, placed = start + VOICEOVER_LEAD_SEC, None
        for a, b in merged + [(horizon, None)]:
            if a - free_from >= need:
                placed = free_from - start
                break
            if b is None or b + 0.2 - start > 12.0:
                break
            free_from = max(free_from, b + 0.2)
        if placed is not None:
            s["_vo_offset"] = round(placed, 2)
            s["_vo_place"] = "pause"
            counts["pause"] += 1
            continue
        prev_end = active[idx - 1]["end"] if idx > 0 else 0.0
        last_speech = max([b for a, b in cues if b <= start + 0.05] or [0.0])
        free_start = max(last_speech + 0.3, prev_end + 0.5, start - 45.0)
        lead = need + 0.3
        if start - free_start >= lead:
            s["start"] = round(start - lead, 2)
            s["duration"] = round(s["end"] - s["start"], 2)
            s["_vo_offset"] = 0.3
            s["_vo_tempo"] = 1.0
            s["_vo_place"] = "lead_in"
            added += lead
            counts["lead_in"] += 1
            continue
        if counts["still"] < max_holds:
            s["_vo_tempo"] = 1.0
            s["_vo_hold"] = round(vo_dur + 0.6, 2)
            s["_vo_place"] = "still"
            added += vo_dur + 0.6
            counts["still"] += 1
            continue
        s["_vo_offset"] = VOICEOVER_LEAD_SEC
        s["_vo_place"] = "over_film"
        counts["over_film"] += 1
    if added > 1.0:
        # give the added time back: end the longest clips earlier, on line breaks, never inside a narration
        inside, clean = speech_index(cues)
        target = original
        total = sum(x["end"] - x["start"] for x in active) + sum(float(x.get("_vo_hold", 0.0)) for x in active)
        for x in sorted(active, key=lambda z: -(z["end"] - z["start"])):
            excess = total - target
            if excess <= 1.0:
                break
            if x.get("exact_boundaries"):
                continue
            keep_until = x["start"] + max(12.0, (x["end"] - x["start"]) * 0.55)
            if x.get("_vo_place") in ("pause", "lead_in", "over_film"):
                keep_until = max(keep_until, x["start"] + float(x.get("_vo_offset", 0.7)) + (get_media_duration(x["voiceover_audio_path"]) or 0) + 1.0)
            want = max(keep_until, x["end"] - excess)
            options = [b for b in clean if want - 4.0 <= b <= x["end"] - 0.5 and b >= keep_until]
            new_end = (min(options, key=lambda b: abs(b - want)) + 0.05) if options and inside(want) else want
            if new_end < x["end"] - 0.5:
                total -= x["end"] - new_end
                x["end"] = round(new_end, 2)
                x["duration"] = round(x["end"] - x["start"], 2)
    return counts


def _move_into_place(src: Path, dest: Path) -> Path:
    """Moves the finished file into the output folder; if the old file is open in a player, uses a new name."""
    try:
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
        return dest
    except PermissionError:
        alt = dest.with_name(f"{dest.stem}_{time.strftime('%H%M%S')}{dest.suffix}")
        shutil.move(str(src), str(alt))
        return alt


def render_summary_video(
    source_video_path: str,
    scenes: List[Dict[str, Any]],
    job_id: str,
    output_filename: Optional[str] = None,
    render_mode: str = "cinematic_nvenc",
    include_voiceover: bool = False,
    normalize_audio: bool = True,
    horizontal_flip: bool = False,
    smooth_audio: bool = True,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    output_dir: Optional[str] = None,
    result_info: Optional[Dict[str, Any]] = None,
    keep_order: bool = False,
    speech_cues: Optional[List[Tuple[float, float]]] = None
) -> str:
    """
    Renders the condensed cut.
      - Every clip is cut and encoded ONCE (fades, mirror and format conversion happen in the same pass),
        several clips in parallel on NVENC.
      - Clips are cached by a hash of their exact parameters, so re-renders reuse only clips that are
        truly identical (toggling scenes or changing settings can never stitch in a stale clip).
      - Chapters are built from the measured clip lengths, so they line up with the video.
    """
    source_path = Path(source_video_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source video not found: {source_path}")

    active = [s for s in scenes if s.get("selected", True)]
    if not active:
        raise ValueError("No scenes selected for rendering.")
    if not keep_order:   # trailers play in their own order; everything else in film order
        active.sort(key=lambda x: x["start"])

    try:
        meta = get_video_metadata(str(source_path))
    except Exception:
        meta = {}
    src_dur = meta.get("duration_sec") or get_media_duration(str(source_path)) or max(s["end"] for s in active)
    has_audio = meta.get("has_audio", True)

    job_temp = TEMP_DIR / job_id / "segments"
    job_temp.mkdir(parents=True, exist_ok=True)

    if horizontal_flip:
        suffix = "_Mirrored"
    elif render_mode == "stream_copy":
        suffix = "_LightningCopy"
    else:
        suffix = "_CineCut_Summary"
    output_filename = sanitize_output_filename(output_filename, f"{source_path.stem}{suffix}.mp4")
    out_dir = Path(output_dir).resolve() if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    final_output_path = out_dir / output_filename

    has_nvenc = check_nvenc_support()
    is_stream_copy = render_mode == "stream_copy" and not horizontal_flip
    reencode_audio = (not is_stream_copy) or include_voiceover or not has_audio
    if include_voiceover:
        fit_voiceovers(active, src_dur)
        placement = place_voiceovers(active, speech_cues)
        if result_info is not None:
            result_info["narration_placement"] = placement

    gpu = get_gpu_name() or "NVIDIA GPU"
    if is_stream_copy:
        engine_label = "Lightning Stream-Copy (no video re-encoding)"
    elif has_nvenc:
        engine_label = f"{gpu} NVENC"
    else:
        engine_label = "CPU (libx264)"

    total = len(active)
    if progress_callback:
        progress_callback(5.0, f"Rendering {total} scenes with {engine_label}...")

    src_sig = _file_sig(source_path)
    lock = threading.Lock()
    done = [0]

    def build_cut_cmd(start: float, dur: float, first: bool, last: bool, use_nvenc: bool, out: Path) -> List[str]:
        inputs = ["-ss", f"{start:.3f}", "-i", str(source_path)]
        maps = ["-map", "0:v:0"]
        if has_audio:
            maps += ["-map", "0:a:0"]
        else:
            inputs += ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo"]
            maps += ["-map", "1:a:0"]
        if is_stream_copy:
            filters: List[str] = []
            codec = ["-c:v", "copy"] + (AUDIO_ARGS if reencode_audio else ["-c:a", "copy"])
        else:
            vf = ["hflip"] if horizontal_flip else []
            af = []
            if smooth_audio:
                vfade, afade = build_transition_filters(dur, first, last)
                vf.append(vfade)
                af.append(afade)
            vf.append("format=yuv420p")
            filters = ["-vf", ",".join(vf)] + (["-af", ",".join(af)] if af else [])
            codec = video_codec_args(use_nvenc) + AUDIO_ARGS
        return [FFMPEG_BIN, "-y", "-hide_banner", *inputs, "-t", f"{dur:.3f}", *maps, *filters, *codec,
                "-avoid_negative_ts", "make_zero", "-max_muxing_queue_size", "4096", str(out)]

    def cut(idx: int) -> Path:
        s = active[idx]
        start = float(s["start"])
        dur = max(0.3, float(s["end"]) - start)
        first, last = idx == 0, idx == total - 1
        key = hashlib.sha1(
            f"{src_sig}|{start:.3f}|{dur:.3f}|{is_stream_copy}|{horizontal_flip}|{smooth_audio}|"
            f"{first}|{last}|{has_nvenc}|{reencode_audio}|{CACHE_VERSION}".encode()).hexdigest()[:16]
        seg = job_temp / f"seg_{key}.mp4"
        if not (seg.exists() and seg.stat().st_size > 1000):
            tmp = job_temp / f"seg_{key}.tmp.mp4"
            res = _run(build_cut_cmd(start, dur, first, last, has_nvenc, tmp))
            if (res.returncode != 0 or not tmp.exists()) and has_nvenc and not is_stream_copy:
                res = _run(build_cut_cmd(start, dur, first, last, False, tmp))  # NVENC fallback to CPU
            if res.returncode != 0 or not tmp.exists():
                raise RenderError(f"Scene {idx + 1} ('{s.get('title', 'Scene')}') could not be cut: {_tail(res.stderr)}")
            os.replace(tmp, seg)

        out = seg
        vo = s.get("voiceover_audio_path")
        if include_voiceover and vo and os.path.exists(vo):
            tempo = float(s.get("_vo_tempo", 1.0))
            offset = float(s.get("_vo_offset", VOICEOVER_LEAD_SEC))
            hold = float(s.get("_vo_hold", 0.0))
            mkey = hashlib.sha1(f"{key}|{_file_sig(Path(vo))}|{tempo}|{offset}|{hold}|{CACHE_VERSION}".encode()).hexdigest()[:16]
            mixed = job_temp / f"mix_{mkey}.mp4"
            if not (mixed.exists() and mixed.stat().st_size > 1000):
                from backend.video_engine.narrator import mix_voiceover_into_segment, narrate_over_hold
                ok = (narrate_over_hold(str(seg), vo, str(mixed), hold) if hold > 0
                      else mix_voiceover_into_segment(str(seg), vo, str(mixed), lead_sec=offset, tempo=tempo))
                if not ok:
                    mixed = None
            if mixed is not None:
                out = mixed

        with lock:
            done[0] += 1
            if progress_callback:
                progress_callback(round(5.0 + done[0] / total * 75.0, 1),
                                  f"Processed scene {done[0]}/{total}: '{s.get('title', 'Scene')}'")
        return out

    workers = RENDER_WORKERS if (has_nvenc or is_stream_copy) else min(2, RENDER_WORKERS)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            segment_files = list(pool.map(cut, range(total)))
    finally:
        for s in active:
            s.pop("_vo_tempo", None)
            s.pop("_vo_offset", None)
            s.pop("_vo_hold", None)
            s.pop("_vo_place", None)

    durations = [round(get_media_duration(str(f)) or (a["end"] - a["start"]), 3)
                 for f, a in zip(segment_files, active)]

    # Concatenate
    concat_txt = job_temp / "concat_list.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for seg in segment_files:
            clean = str(seg.resolve()).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{clean}'\n")

    if progress_callback:
        progress_callback(82.0, "Stitching the timeline...")
    run_id = hashlib.sha1(f"{job_id}|{time.time()}".encode()).hexdigest()[:10]
    raw_output_path = job_temp / f"raw_{run_id}.mp4"
    norm_output_path = job_temp / f"norm_{run_id}.mp4"
    final_tmp = job_temp / f"final_{run_id}.mp4"

    res = _run([FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
                "-map", "0", "-c", "copy", "-movflags", "+faststart", str(raw_output_path)])
    if res.returncode != 0 or not raw_output_path.exists():
        res = _run([FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
                    *video_codec_args(has_nvenc), *AUDIO_ARGS, "-movflags", "+faststart", str(raw_output_path)])
        if res.returncode != 0 or not raw_output_path.exists():
            raise RenderError(f"Could not stitch the clips together: {_tail(res.stderr)}")

    target = raw_output_path
    if normalize_audio:
        if progress_callback:
            progress_callback(90.0, "Applying EBU R128 loudness normalization...")
        res = _run([FFMPEG_BIN, "-y", "-i", str(raw_output_path), "-map", "0", "-c:v", "copy",
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(AUDIO_SAMPLE_RATE),
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(norm_output_path)])
        if res.returncode == 0 and norm_output_path.exists():
            target = norm_output_path

    if progress_callback:
        progress_callback(96.0, "Embedding chapter markers...")
    meta_file = job_temp / "chapters.meta"
    create_ffmetadata_chapters(active, str(meta_file), durations=durations)
    if not embed_chapters_into_video(str(target), str(meta_file), str(final_tmp)):
        final_tmp = target
    final_path = _move_into_place(final_tmp, final_output_path)

    for leftover in (raw_output_path, norm_output_path):
        try:
            if leftover.exists():
                leftover.unlink()
        except OSError:
            pass

    if result_info is not None:
        chapters, t = [], 0.0
        for s, d in zip(active, durations):
            act = s.get("act") if isinstance(s.get("act"), str) else ""
            title = f"{act.split(':')[0]}: {s.get('title', 'Scene')}" if act else s.get("title", "Scene")
            chapters.append({"title": title, "start": round(t, 2), "end": round(t + d, 2)})
            t += d
        result_info.update({"durations": durations, "chapters": chapters, "engine": engine_label,
                            "output_file": str(final_path), "total_duration": round(t, 2)})

    if progress_callback:
        progress_callback(100.0, f"Summary complete! Saved to {final_path.name}")
    return str(final_path)
