"""
End card for every video CineCut makes: who the source belongs to (the credit line) and that the summary and
voice were made with AI, as India's 2026 IT Rules expect for synthetic content.

The card is drawn with libass (so Hindi renders), matched to the video's size, frame rate and audio format, and
joined to the end. It is first joined without re-encoding (fast); if the result does not come out the right
length it is joined again with a full re-encode.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import FFMPEG_BIN

AI_LINE = {"English": "Summary, narration and voice made with AI (CineCut). Not affiliated with the rights holders.",
           "Hindi": "सार, कथन और आवाज़ AI से बनाए गए हैं (CineCut)। अधिकार धारकों से इसका कोई संबंध नहीं है।"}


def _probe(path: str) -> Dict[str, Any]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,sample_rate,channels:format=duration", "-of", "json", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return {}
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    num, _, den = (v.get("r_frame_rate") or "25/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 25.0
    return {"w": int(v.get("width") or 1280), "h": int(v.get("height") or 720), "fps": round(fps, 3) if fps > 1 else 25.0,
            "rate": int((a or {}).get("sample_rate") or 48000), "ch": int((a or {}).get("channels") or 2), "audio": a is not None,
            "duration": float((data.get("format") or {}).get("duration") or 0)}


def _esc(t: str) -> str:
    return (t or "").replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _ass(w: int, h: int, seconds: float, credit: str, ai_line: str, title: str) -> str:
    s = h / 1080.0
    size = lambda px: max(12, int(px * s))
    margin = int(w * 0.08)
    end = f"0:00:{seconds:05.2f}"
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Kicker,Nirmala UI,{size(34)},&H0047B5FF,&H0047B5FF,&H00000000,&H00000000,-1,0,0,0,100,100,3,0,1,0,0,7,{margin},{margin},0,1
Style: Title,Nirmala UI,{size(60)},&H00F4F1EC,&H00F4F1EC,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,{margin},{margin},0,1
Style: Body,Nirmala UI,{size(40)},&H00DCD6CF,&H00DCD6CF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,{margin},{margin},0,1
Style: Small,Nirmala UI,{size(30)},&H009A948C,&H009A948C,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,{margin},{margin},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,{end},Kicker,,0,0,0,,{{\\pos({margin},{int(h * 0.26)})\\fad(300,0)}}CREDITS
Dialogue: 0,0:00:00.00,{end},Title,,0,0,0,,{{\\pos({margin},{int(h * 0.32)})\\fad(300,0)}}{_esc(title)}
Dialogue: 0,0:00:00.00,{end},Body,,0,0,0,,{{\\pos({margin},{int(h * 0.48)})\\fad(500,0)}}{_esc(credit)}
Dialogue: 0,0:00:00.00,{end},Small,,0,0,0,,{{\\pos({margin},{int(h * 0.72)})\\fad(700,0)}}{_esc(ai_line)}
"""


def _run(cmd, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def append_end_card(video_path: str, credit: str, title: str = "", language: str = "English", seconds: float = 5.0) -> bool:
    """Adds the end card to video_path in place. Returns False (leaving the video untouched) if it could not."""
    p = Path(video_path)
    if not p.exists():
        return False
    info = _probe(str(p))
    if not info.get("duration"):
        return False
    # a short, safe folder name: a long title cut at a space ends in a space, which Windows drops from folder names
    work = p.parent / f".endcard_{hashlib.sha1(str(p.resolve()).encode('utf-8')).hexdigest()[:12]}"
    try:
        work.mkdir(parents=True, exist_ok=True)
        ai_line = AI_LINE.get(language, AI_LINE["English"]) + ("" if language == "English" else "  " + AI_LINE["English"])
        (work / "card.ass").write_text(_ass(info["w"], info["h"], seconds, credit, ai_line, title or "CineCut summary"), encoding="utf-8")
        layout = "mono" if info["ch"] == 1 else "stereo"
        cmd = [FFMPEG_BIN, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x11151c:s={info['w']}x{info['h']}:r={info['fps']}:d={seconds}"]
        if info["audio"]:
            cmd += ["-f", "lavfi", "-t", str(seconds), "-i", f"anullsrc=r={info['rate']}:cl={layout}"]
        cmd += ["-vf", "ass=card.ass", "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-r", str(info["fps"])]
        if info["audio"]:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", str(info["rate"]), "-ac", str(info["ch"]), "-shortest"]
        cmd += ["-t", str(seconds), "card.mp4"]
        if _run(cmd, cwd=str(work)).returncode != 0:
            return False
        out = work / "joined.mp4"
        (work / "list.txt").write_text(f"file '{p.resolve().as_posix()}'\nfile '{(work / 'card.mp4').resolve().as_posix()}'\n", encoding="utf-8")
        ok = _run([FFMPEG_BIN, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy",
                   "-movflags", "+faststart", str(out)], cwd=str(work)).returncode == 0
        want = info["duration"] + seconds
        if not ok or abs(_probe(str(out)).get("duration", 0) - want) > 1.5:
            streams = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]" if info["audio"] else "[0:v][1:v]concat=n=2:v=1:a=0[v]"
            cmd = [FFMPEG_BIN, "-y", "-v", "error", "-i", str(p), "-i", "card.mp4", "-filter_complex", streams, "-map", "[v]"]
            cmd += (["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if info["audio"] else [])
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
            if _run(cmd, cwd=str(work)).returncode != 0:
                return False
        os.replace(out, p)
        return True
    except Exception:                  # the card is a courtesy: a render never fails because of it
        return False
    finally:
        for f in work.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass


def credit_for(rights: Optional[Dict[str, Any]], title: str) -> str:
    """The credit line for a job from its rights decision."""
    r = rights or {}
    status = r.get("status")
    if status == "cleared" and r.get("attribution"):
        return f"Source: {r['attribution']}"
    if status == "declared":
        return f"Source: {title}, used by its owner or with the owner's permission (declared in CineCut)."
    return f"Source: {title}. Private viewing only; not for sharing."
