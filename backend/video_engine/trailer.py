"""
Trailer mode.

Builds a 30-180 second trailer the way trailer editors structure one, from the film itself:
  1. Setup   (~35%): 3-6 complete spoken lines from the core story beats of the first part of the film.
  2. Build   (~30%): short, louder moments (about 3 s) from the middle, spread out in time.
  3. Montage (~22%): quick shots (about 1.6 s) from the loudest, fastest-cut stretches (action and song
                     sequences first), ordered from quieter to louder.
  4. Button  (~13%): one intriguing line, then a title card with the film's name.
Spoiler-free by default: nothing after 65% of the film is used. Spoken setup lines start and end on line
breaks, and the clips play in trailer order, not film order. A 9:16 version can be made for Reels/Shorts.
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import FFMPEG_BIN, AUDIO_SAMPLE_RATE, TEMP_DIR, OUTPUT_DIR
from backend.video_engine.audio_analyzer import EnergyIndex
from backend.video_engine.flavor_detector import ANY_TAG, detect_flavor_segments
from backend.video_engine.probe import format_seconds, check_nvenc_support
from backend.video_engine.subtitle_parser import score_dialogue_significance
from backend.video_engine.summarizer import speech_index

FONT_CANDIDATES = [r"C:\Windows\Fonts\NirmalaB.ttf", r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\arialbd.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
CARD_SEC = 3.5

Window = Tuple[float, float, Dict[str, Any]]


def _clean(text: Optional[str]) -> str:
    return " ".join(ANY_TAG.sub(" ", text or "").split())


def _sentences(subs: List[Dict[str, Any]], lo: float, hi: float) -> List[Dict[str, Any]]:
    """Spoken lines inside [lo, hi]; back-to-back caption lines are joined into sentences of up to 8 s."""
    lines = sorted([s for s in subs if s["start"] >= lo and s["end"] <= hi and s["end"] - s["start"] <= 12.0
                    and _clean(s.get("text"))], key=lambda s: s["start"])
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for s in lines:
        text = _clean(s["text"])
        if cur and s["start"] - cur["end"] < 0.45 and s["end"] - cur["start"] <= 8.0 \
                and not cur["text"].endswith((".", "?", "!", "\u0964")):
            cur["end"], cur["text"] = s["end"], cur["text"] + " " + text
            continue
        if cur:
            out.append(cur)
        cur = {"start": s["start"], "end": s["end"], "text": text}
    if cur:
        out.append(cur)
    return [x for x in out if 1.8 <= x["end"] - x["start"] <= 8.5]


def _line_score(x: Dict[str, Any], energy: EnergyIndex) -> float:
    text = x["text"]
    score = float(score_dialogue_significance(text, x["end"] - x["start"]))
    if "?" in text or "!" in text:
        score += 8.0
    if len(text.split()) < 3:
        score -= 15.0
    return score + 0.15 * energy.percentile(x["start"], x["end"])


def _free(a: float, b: float, used: List[Tuple[float, float]], gap: float = 1.0) -> bool:
    return all(b + gap <= u0 or a - gap >= u1 for u0, u1 in used)


def _pick(cands: List[Tuple[float, float, float, Dict[str, Any]]], n: int, used: List[Tuple[float, float]],
          min_sep: float) -> List[Tuple[float, float, float, Dict[str, Any]]]:
    chosen: List[Tuple[float, float, float, Dict[str, Any]]] = []
    for c in sorted(cands, key=lambda x: -x[0]):
        if len(chosen) >= n:
            break
        _, a, b, _ = c
        if not _free(a, b, used) or any(abs(a - k[1]) < min_sep for k in chosen):
            continue
        chosen.append(c)
        used.append((a, b))
    return chosen


def _moment_windows(shot_cuts: List[float], lo: float, hi: float, length: float, energy: EnergyIndex,
                    has_energy: bool) -> List[Tuple[float, float]]:
    """One window of `length` seconds inside each detected shot (the loudest part of it); long shots give two."""
    cuts = [lo] + [c for c in sorted(shot_cuts or []) if lo < c < hi] + [hi]
    wins: List[Tuple[float, float]] = []
    for a, b in zip(cuts, cuts[1:]):
        a2, b2 = a + 0.15, b - 0.15
        if b2 - a2 < length * 0.8:
            continue
        spans = [(a2, b2)] if b2 - a2 < 6 * length else [(a2, (a2 + b2) / 2), ((a2 + b2) / 2, b2)]
        for s0, s1 in spans:
            span_len = min(length, s1 - s0)
            start = energy.loudest_window(s0, s1, span_len) if has_energy else s0
            start = max(s0, min(start, s1 - span_len))
            wins.append((start, start + span_len))
    return wins


def plan_trailer(job: Dict[str, Any], length_sec: int = 90, spoiler_free: bool = True) -> Dict[str, Any]:
    dur = float(job["metadata"]["duration_sec"])
    subs = job.get("subtitles") or []
    shot_cuts = job.get("shot_cuts") or []
    silences = job.get("silences") or []
    energy_values = job.get("energy") or []
    energy = EnergyIndex(energy_values)
    has_energy = bool(energy_values)
    region_end = dur * (0.65 if spoiler_free else 0.97)
    region_lo = min(max(20.0, dur * 0.02), region_end * 0.1)
    inside, _ = speech_index([(s["start"], s["end"]) for s in subs if _clean(s.get("text"))])
    body = max(20.0, float(length_sec) - CARD_SEC)
    used: List[Tuple[float, float]] = []
    parts: Dict[str, List[Dict[str, Any]]] = {"setup": [], "build": [], "montage": [], "button": []}

    # 1. Setup: complete lines from the core story beats in the first half of the allowed part of the film
    setup_budget = body * 0.35
    half = region_lo + (region_end - region_lo) * 0.5
    beats = [m for m in ((job.get("essence") or {}).get("milestones") or [])
             if region_lo <= float(m.get("target_time_sec", m.get("start_sec", 0)) or 0) <= half
             and int(m.get("tier", 1) or 1) == 1]
    beats.sort(key=lambda m: float(m.get("target_time_sec", m.get("start_sec", 0)) or 0))
    want = max(2, min(6, int(round(setup_budget / 5.5))))
    if len(beats) > want:
        step = len(beats) / want
        beats = [beats[int(i * step)] for i in range(want)]
    windows = [(max(region_lo, float(m.get("start_sec", m.get("target_time_sec", 0))) - 20.0),
                min(half, float(m.get("end_sec", float(m.get("target_time_sec", 0)) + 90.0)) + 20.0)) for m in beats]
    if not windows:
        step = (half - region_lo) / want
        windows = [(region_lo + i * step, region_lo + (i + 1) * step) for i in range(want)]
    spent = 0.0
    for a, b in windows:
        if spent >= setup_budget:
            break
        lines = [x for x in _sentences(subs, a, b) if _free(x["start"] - 0.15, x["end"] + 0.25, used)]
        if not lines:
            continue
        best = max(lines, key=lambda x: _line_score(x, energy))
        s, e = max(0.0, best["start"] - 0.15), best["end"] + 0.25
        used.append((s, e))
        parts["setup"].append({"start": s, "end": e, "text": best["text"]})
        spent += e - s
    if spent < setup_budget * 0.8:   # few core beats early in the film: add its other strongest lines
        for x in sorted(_sentences(subs, region_lo, half), key=lambda x: -_line_score(x, energy)):
            if spent >= setup_budget:
                break
            if not _free(x["start"] - 0.15, x["end"] + 0.25, used, gap=45.0):
                continue
            s, e = max(0.0, x["start"] - 0.15), x["end"] + 0.25
            used.append((s, e))
            parts["setup"].append({"start": s, "end": e, "text": x["text"]})
            spent += e - s
        parts["setup"].sort(key=lambda x: x["start"])

    # Detected action and song stretches make the best montage material
    hot: List[Tuple[float, float]] = []
    for flavor in ("action", "song"):
        try:
            hot += [(g["start"], g["end"]) for g in detect_flavor_segments(flavor, subs, energy_values, shot_cuts, silences, dur)]
        except Exception:
            pass

    def moment_score(a: float, b: float) -> float:
        loud = energy.percentile(a, b) if has_energy else 50.0
        cuts_near = sum(1 for c in shot_cuts if a - 10.0 <= c <= b + 10.0)
        score = loud + 4.0 * min(cuts_near, 6)
        if any(h0 <= a and b <= h1 for h0, h1 in hot):
            score += 20.0
        if subs and inside((a + b) / 2):
            score -= 25.0          # mid-sentence speech sounds choppy in a fast cut
        return score

    # 2. Build: louder moments from the middle, spread out
    build_n = max(3, int(round(body * 0.30 / 3.0)))
    lo_b, hi_b = region_lo + (region_end - region_lo) * 0.3, region_end
    cands = [(moment_score(a, b), a, b, {}) for a, b in _moment_windows(shot_cuts, lo_b, hi_b, 3.0, energy, has_energy)]
    for _, a, b, _m in sorted(_pick(cands, build_n, used, (hi_b - lo_b) / (build_n * 2.0)), key=lambda x: x[1]):
        parts["build"].append({"start": a, "end": b, "text": ""})

    # 3. Montage: quick shots from anywhere allowed, quieter to louder
    mont_n = max(4, int(round(body * 0.22 / 1.6)))
    cands = [(moment_score(a, b), a, b, {}) for a, b in _moment_windows(shot_cuts, region_lo, region_end, 1.6, energy, has_energy)]
    picked = _pick(cands, mont_n, used, (region_end - region_lo) / (mont_n * 3.0))
    for c in sorted(picked, key=lambda x: energy.percentile(x[1], x[2]) if has_energy else x[1]):
        parts["montage"].append({"start": c[1], "end": c[2], "text": ""})

    # 4. Button: one short, intriguing line from the later part of the allowed film
    lines = [x for x in _sentences(subs, region_lo + (region_end - region_lo) * 0.4, region_end)
             if _free(x["start"] - 0.15, x["end"] + 0.25, used) and x["end"] - x["start"] <= 6.0]
    if lines:
        best = max(lines, key=lambda x: _line_score(x, energy) + (10.0 if 2.0 <= x["end"] - x["start"] <= 5.0 else 0.0))
        parts["button"].append({"start": max(0.0, best["start"] - 0.15), "end": best["end"] + 0.35, "text": best["text"]})

    total = sum(x["end"] - x["start"] for part in parts.values() for x in part)
    if total < body * 0.92:   # still short: more build-up moments, closer together
        need = int((body - total) / 3.0) + 1
        more = [(moment_score(a, b), a, b, {}) for a, b in _moment_windows(shot_cuts, lo_b, hi_b, 3.0, energy, has_energy)]
        for _, a, b, _m in _pick(more, need, used, 20.0):
            parts["build"].append({"start": a, "end": b, "text": ""})
        parts["build"].sort(key=lambda x: x["start"])

    scenes: List[Dict[str, Any]] = []
    labels = {"setup": "Setup", "build": "Build-up", "montage": "Montage", "button": "Closing line"}
    for part in ("setup", "build", "montage", "button"):
        for n, x in enumerate(parts[part], 1):
            text = x["text"]
            scenes.append({"act": f"Trailer: {labels[part]}", "start": round(x["start"], 2), "end": round(x["end"], 2),
                           "duration": round(x["end"] - x["start"], 2), "title": f"{labels[part]} {n}" + (f": {text[:50]}" if text else ""),
                           "start_formatted": format_seconds(x["start"]), "end_formatted": format_seconds(x["end"]),
                           "dialogue": text, "selected": True, "exact_boundaries": True, "source": "trailer", "part": part})
    return {"scenes": scenes, "region_end_pct": round(100 * region_end / dur), "body_sec": round(sum(s["duration"] for s in scenes), 1)}


def _probe_video(path: str) -> Tuple[int, int, str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                          "-of", "csv=p=0", path], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip().split(",")
    return int(out[0]), int(out[1]), out[2] if len(out) > 2 and out[2] else "30"


def _video_args() -> List[str]:
    return (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0"] if check_nvenc_support()
            else ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])


def _run(cmd: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd)


def make_trailer(job: Dict[str, Any], length_sec: int = 90, spoiler_free: bool = True, vertical: bool = False) -> Dict[str, Any]:
    from backend.video_engine.renderer import render_summary_video, sanitize_output_filename
    from backend.video_engine.shorts_generator import BLURRED_WINGS

    plan = plan_trailer(job, length_sec, spoiler_free)
    scenes = plan["scenes"]
    if len(scenes) < 4:
        raise ValueError("Not enough material for a trailer in this video.")
    work = TEMP_DIR / job["job_id"] / "trailer"
    work.mkdir(parents=True, exist_ok=True)
    body = render_summary_video(job["video_path"], scenes, job["job_id"], output_filename="trailer_body.mp4",
                                render_mode="cinematic_nvenc", include_voiceover=False, normalize_audio=False,
                                smooth_audio=True, output_dir=str(work), keep_order=True)

    # Title card: the film's name on black, faded in and out, then the whole trailer is loudness-normalized
    title = (job.get("film_title") or Path(job["video_path"]).stem).strip()
    (work / "title.txt").write_text(title.upper() if title.isascii() else title, encoding="utf-8")
    (work / "tag.txt").write_text("A CineCut trailer" + (" - no spoilers" if spoiler_free else ""), encoding="utf-8")
    font = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
    if font:
        shutil.copyfile(font, work / "title_font.ttf")
    w, h, fps = _probe_video(body)
    size = max(24, min(h // 8, int(w * 1.5 / max(8, len(title)))))
    fontopt = "fontfile=title_font.ttf:" if font else ""
    card = (f"[1:v]drawtext={fontopt}textfile=title.txt:fontcolor=white:fontsize={size}:x=(w-text_w)/2:y=(h-text_h)/2-{h // 18},"
            f"drawtext={fontopt}textfile=tag.txt:fontcolor=0xBBBBBB:fontsize={max(14, h // 30)}:x=(w-text_w)/2:y=h/2+{h // 10},"
            f"fade=t=in:st=0:d=0.6,fade=t=out:st={CARD_SEC - 0.7:.1f}:d=0.6,format=yuv420p,setsar=1[card];"
            f"[0:v]format=yuv420p,setsar=1[body];"
            f"[0:a]aresample={AUDIO_SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[ba];"
            f"[2:a]aresample={AUDIO_SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[ca];"
            f"[body][ba][card][ca]concat=n=2:v=1:a=1[v][a];[a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample={AUDIO_SAMPLE_RATE}[an]")
    safe = sanitize_output_filename(None, f"{title}_trailer_{int(length_sec)}s{'_9x16' if vertical else ''}.mp4")
    landscape = work / "trailer_16x9.mp4"
    res = _run([FFMPEG_BIN, "-y", "-i", "trailer_body.mp4",
                "-f", "lavfi", "-t", f"{CARD_SEC}", "-i", f"color=c=black:s={w}x{h}:r={fps}",
                "-f", "lavfi", "-t", f"{CARD_SEC}", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo",
                "-filter_complex", card, "-map", "[v]", "-map", "[an]", *_video_args(),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", landscape.name], cwd=str(work))
    if res.returncode != 0 or not landscape.exists():
        raise RuntimeError("Title card step failed: " + (res.stderr.strip().splitlines() or ["?"])[-1])
    final = (Path(job["private_dir"]) if job.get("private_dir") else OUTPUT_DIR) / safe   # private viewing stays in the job folder
    final.parent.mkdir(parents=True, exist_ok=True)
    if vertical:
        res = _run([FFMPEG_BIN, "-y", "-i", landscape.name, "-vf", BLURRED_WINGS + ",format=yuv420p", *_video_args(),
                    "-c:a", "copy", "-movflags", "+faststart", str(final)], cwd=str(work))
        if res.returncode != 0:
            raise RuntimeError("9:16 step failed: " + (res.stderr.strip().splitlines() or ["?"])[-1])
    else:
        shutil.copyfile(landscape, final)
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip() or 0)
    counts = {p: sum(1 for s in scenes if s["part"] == p) for p in ("setup", "build", "montage", "button")}
    return {"file": str(final), "duration_sec": round(length, 1), "duration_formatted": format_seconds(length),
            "parts": counts, "spoiler_free": spoiler_free, "uses_film_up_to_pct": plan["region_end_pct"], "vertical": vertical,
            "clips": [{"part": s["part"], "from": s["start_formatted"], "to": s["end_formatted"], "line": s["dialogue"]} for s in scenes]}
