import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from backend.config import FFMPEG_BIN, TEMP_DIR, OUTPUT_DIR
from backend.video_engine.probe import (get_video_metadata, format_seconds, check_nvenc_support,
                                        extract_subtitles_to_srt, get_media_duration)
from backend.video_engine.subtitle_parser import group_dialogue_into_scenes, score_dialogue_significance, parse_srt_file
from backend.video_engine.summarizer import compute_local_heuristic_summary
from backend.video_engine.subtitles_and_chapters import create_ffmetadata_chapters, embed_chapters_into_video
from backend.video_engine.renderer import video_codec_args, AUDIO_ARGS, _run, _tail, _move_into_place

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def parse_episode_identifier(filename: str) -> Dict[str, Any]:
    """Extracts season/episode numbers and a clean title (S01E02, 1x02, Episode 2, Ep02)."""
    stem = Path(filename).stem
    season_num, episode_num, clean_title = 1, 1, stem
    m1 = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', stem)
    if m1:
        season_num, episode_num = int(m1.group(1)), int(m1.group(2))
        clean_title = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}', '', stem).strip(" .-_")
    else:
        m2 = re.search(r'(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)', stem)
        if m2:
            season_num, episode_num = int(m2.group(1)), int(m2.group(2))
            clean_title = re.sub(r'\d{1,2}x\d{1,3}', '', stem).strip(" .-_")
        else:
            m3 = re.search(r'(?:episode|ep)[\s_.-]*(\d{1,3})', stem, re.I)
            if m3:
                episode_num = int(m3.group(1))
                clean_title = re.sub(r'(?:episode|ep)[\s_.-]*\d{1,3}', '', stem, flags=re.I).strip(" .-_")
    clean_title = re.sub(r'[._]+', ' ', clean_title).strip() or f"Episode {episode_num}"
    return {"season_num": season_num, "episode_num": episode_num, "clean_title": clean_title, "raw_stem": stem}


def scan_season_folder(folder_path: str) -> Dict[str, Any]:
    folder = Path(folder_path.strip().strip('"')).resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"Directory not found: {folder}")
    files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS]
    if not files:
        raise ValueError(f"No video files found in {folder}. Supported: {sorted(VIDEO_EXTENSIONS)}")

    episodes, total = [], 0.0
    for file_path in files:
        ep = parse_episode_identifier(file_path.name)
        try:
            meta = get_video_metadata(str(file_path))
            dur = meta.get("duration_sec", 0.0)
            info = {"resolution": f"{meta.get('width')}x{meta.get('height')}", "fps": meta.get("fps", 24.0),
                    "has_subtitles": meta.get("has_subtitles", False), "probe_ok": True}
        except Exception:
            dur, info = 0.0, {"resolution": "unknown", "fps": 24.0, "has_subtitles": False, "probe_ok": False}
        total += dur
        episodes.append({"path": str(file_path), "file_name": file_path.name, "season": ep["season_num"],
                         "episode": ep["episode_num"], "title": ep["clean_title"], "duration_sec": dur,
                         "duration_formatted": format_seconds(dur), **info})
    episodes = [e for e in episodes if e["duration_sec"] > 0]
    if not episodes:
        raise ValueError("None of the video files in this folder could be read.")
    episodes.sort(key=lambda x: (x["season"], x["episode"], x["file_name"]))
    return {"folder_path": str(folder), "season_num": episodes[0]["season"], "total_episodes": len(episodes),
            "total_runtime_sec": round(total, 2), "total_runtime_formatted": format_seconds(total), "episodes": episodes}


def budget_season_episodes(episodes: List[Dict[str, Any]], target_season_minutes: float = 45.0) -> List[Dict[str, Any]]:
    """Premiere +15%, mid-season +10%, finale +25%; other episodes share the rest equally."""
    n = len(episodes)
    if n == 0:
        return []
    mid = n // 2
    weights = []
    for i in range(n):
        w = 1.0
        if i == 0:
            w += 0.15
        elif i == n - 1:
            w += 0.25
        elif i == mid and n > 2:
            w += 0.10
        weights.append(w)
    total_w, target = sum(weights), target_season_minutes * 60.0
    out = []
    for ep, w in zip(episodes, weights):
        alloc = min(round(w / total_w * target, 1), ep.get("duration_sec", target) * 0.9)
        e = dict(ep)
        e.update({"allocated_sec": alloc, "allocated_formatted": format_seconds(alloc), "weight_pct": round(w / total_w * 100, 1)})
        out.append(e)
    return out


def assemble_season_recap(batch_id: str, folder_path: str, episodes: List[Dict[str, Any]],
                          target_season_minutes: float = 45.0, preset: str = "story_focused",
                          render_mode: str = "cinematic_nvenc",
                          progress_callback: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """Curates every episode and stitches one season recap with a chapter per episode."""
    budgeted = budget_season_episodes(episodes, target_season_minutes)
    season_num = episodes[0]["season"] if episodes else 1
    batch_temp = TEMP_DIR / batch_id
    seg_dir = batch_temp / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    has_nvenc = check_nvenc_support()
    is_stream_copy = render_mode == "stream_copy"
    total_eps = len(budgeted)
    segment_files: List[Path] = []
    chapter_markers: List[Dict[str, Any]] = []
    warnings: List[str] = []
    timeline = 0.0

    if progress_callback:
        progress_callback(5.0, f"Starting Season {season_num} recap ({total_eps} episodes)...")

    for ep_idx, ep in enumerate(budgeted):
        ep_num, ep_title, ep_path = ep["episode"], ep.get("title", f"Episode {ep['episode']}"), ep["path"]
        ep_dur, ep_budget = ep.get("duration_sec", 0.0), ep["allocated_sec"]
        if progress_callback:
            progress_callback(5.0 + ep_idx / total_eps * 75.0,
                              f"Curating episode {ep_num} ({ep_idx + 1}/{total_eps}): '{ep_title}' ({format_seconds(ep_budget)})...")

        srt_temp = batch_temp / f"ep_{ep_idx:03d}.srt"
        subs = parse_srt_file(str(srt_temp)) if extract_subtitles_to_srt(ep_path, str(srt_temp)) else []
        candidates = group_dialogue_into_scenes(subs) if subs else []
        for c in candidates:
            c["dialogue_score"] = score_dialogue_significance(c.get("dialogue", ""), c.get("duration", 0.0))

        curated = compute_local_heuristic_summary(ep_dur, ep_budget, candidates, [], [], preset, "full_cut")
        if not curated:
            chunk = ep_budget / 3.0
            curated = [{"start": ep_dur * p, "end": ep_dur * p + chunk, "duration": chunk, "title": t}
                       for p, t in ((0.10, "Intro"), (0.50, "Midpoint"), (0.85, "Climax"))]

        chapter_start = timeline
        for sc_idx, scene in enumerate(curated):
            start, dur = scene["start"], scene["end"] - scene["start"]
            seg_file = seg_dir / f"ep{ep_idx:03d}_seg{sc_idx:02d}.mp4"
            if not (seg_file.exists() and seg_file.stat().st_size > 1000):
                def cmd_for(use_nvenc: bool) -> List[str]:
                    codec = ["-c", "copy"] if is_stream_copy else ["-vf", "format=yuv420p", *video_codec_args(use_nvenc), *AUDIO_ARGS]
                    return [FFMPEG_BIN, "-y", "-ss", f"{start:.3f}", "-i", ep_path, "-t", f"{dur:.3f}",
                            "-map", "0:v:0", "-map", "0:a:0?", *codec, "-avoid_negative_ts", "make_zero", str(seg_file)]
                res = _run(cmd_for(has_nvenc))
                if res.returncode != 0 and has_nvenc and not is_stream_copy:
                    res = _run(cmd_for(False))
                if res.returncode != 0 or not seg_file.exists():
                    warnings.append(f"Episode {ep_num} clip {sc_idx + 1} skipped: {_tail(res.stderr, 2)}")
                    continue
            measured = get_media_duration(str(seg_file)) or dur
            segment_files.append(seg_file)
            timeline += measured
        if timeline > chapter_start:
            chapter_markers.append({"title": f"Episode {ep_num:02d}: {ep_title}", "start": round(chapter_start, 2),
                                    "end": round(timeline, 2), "duration": round(timeline - chapter_start, 2)})

    if not segment_files:
        raise RuntimeError("No clips could be cut from this season. " + " ".join(warnings[:3]))

    if progress_callback:
        progress_callback(82.0, "Stitching the season recap...")
    concat_txt = batch_temp / "season_concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for seg in segment_files:
            f.write("file '" + str(seg.resolve()).replace("\\", "/").replace("'", "'\\''") + "'\n")

    output_filename = f"Season_{season_num:02d}_Recap_{target_season_minutes:g}min.mp4"
    raw = batch_temp / f"raw_{output_filename}"
    res = _run([FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy",
                "-movflags", "+faststart", str(raw)])
    if res.returncode != 0 or not raw.exists():
        res = _run([FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-vf", "format=yuv420p",
                    *video_codec_args(has_nvenc), *AUDIO_ARGS, "-movflags", "+faststart", str(raw)])
        if res.returncode != 0 or not raw.exists():
            raise RuntimeError(f"Could not stitch the season recap: {_tail(res.stderr)}")

    if progress_callback:
        progress_callback(95.0, "Embedding episode chapters...")
    meta_file = batch_temp / "season_chapters.meta"
    create_ffmetadata_chapters(chapter_markers, str(meta_file), durations=[c["duration"] for c in chapter_markers],
                               use_act_prefix=False)
    final_tmp = batch_temp / f"final_{output_filename}"
    source = final_tmp if embed_chapters_into_video(str(raw), str(meta_file), str(final_tmp)) else raw
    final_output = _move_into_place(source, OUTPUT_DIR / output_filename)
    if raw.exists():
        try:
            raw.unlink()
        except OSError:
            pass

    if progress_callback:
        progress_callback(100.0, f"Season {season_num} recap complete: {final_output.name}")
    return {"success": True, "output_file": str(final_output), "filename": final_output.name, "season_num": season_num,
            "total_episodes": total_eps, "chapters": chapter_markers, "warnings": warnings,
            "total_duration_sec": round(timeline, 2), "total_duration_formatted": format_seconds(timeline)}
