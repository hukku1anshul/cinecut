"""
Disclosure slate and counter-notification DRAFT.

The previous version produced a "ready-to-submit" DMCA counter-notice that asserted facts the app never
checked (e.g. "less than 20% of the work" and "horizontal reflection active") under penalty of perjury.
Knowingly false counter-notices create liability under 17 U.S.C. 512(f) and reveal the sender's identity.
This version only states facts measured from the project and makes the user confirm everything.
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from backend.config import FFMPEG_BIN

SLATE_TEXT = (
    "This video contains short excerpts used for commentary and review.\n"
    "All rights in the original work belong to their respective owners.\n"
    "Narration and analysis are original to this channel."
)


def generate_fair_use_slate_video(output_mp4_path: str, duration_sec: float = 4.0) -> bool:
    """Renders a 1080p disclosure slate (a courtesy notice; it does not create any legal protection)."""
    out = Path(output_mp4_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent
    (work / "slate_text.txt").write_text(SLATE_TEXT, encoding="utf-8")
    font_src = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    font_arg = ""
    if font_src.exists():
        shutil.copyfile(font_src, work / "slate_font.ttf")
        font_arg = "fontfile=slate_font.ttf:"
    draw = (f"drawtext={font_arg}textfile=slate_text.txt:fontcolor=white:fontsize=40:line_spacing=18:"
            f"x=(w-text_w)/2:y=(h-text_h)/2")
    base = [FFMPEG_BIN, "-y", "-f", "lavfi", "-i", f"color=c=0x0a0f1d:s=1920x1080:d={duration_sec}:r=24",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration_sec}"]
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out.name]
    try:
        res = subprocess.run(base + ["-vf", draw] + tail, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(work))
        if res.returncode != 0:
            res = subprocess.run(base + tail, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(work))
        return res.returncode == 0 and out.exists()
    except Exception:
        return False


def generate_dmca_counter_notification(
    movie_title: str,
    total_scenes: int,
    vo_coverage_pct: float,
    max_clip_sec: float = 7.0,
    has_horizontal_flip: bool = False,
    longest_clip_sec: Optional[float] = None,
    source_share_pct: Optional[float] = None,
    cut_duration_formatted: Optional[str] = None
) -> str:
    """Returns a counter-notification DRAFT with the statutory elements and measured project facts."""
    clean_title = (Path(movie_title).name[:-len(Path(movie_title).suffix)] if Path(movie_title).suffix.lower() in (".mp4", ".mkv", ".mov", ".webm", ".avi") else movie_title) if movie_title else "the work"
    facts = [f"- Scenes in the cut: {total_scenes}"]
    if cut_duration_formatted:
        facts.append(f"- Length of the cut: {cut_duration_formatted}")
    if longest_clip_sec is not None:
        facts.append(f"- Longest continuous clip: {longest_clip_sec:.1f} seconds")
    facts.append(f"- Clip-length cap setting: {max_clip_sec:.1f} seconds")
    if source_share_pct is not None:
        facts.append(f"- Share of the source film used: {source_share_pct:.1f}%")
    facts.append(f"- Scenes with narration or commentary: {vo_coverage_pct:.0f}%")
    facts.append(f"- Mirrored image: {'yes (style only, not a legal factor)' if has_horizontal_flip else 'no'}")

    return f"""DRAFT - NOT READY TO SEND - READ EVERYTHING FIRST
================================================================================
Counter-notification draft for: "{clean_title}"

BEFORE YOU USE THIS
- Only send a counter-notification if you honestly believe the removal was a mistake or a
  misidentification: for example you own or licensed the footage, or the use is clearly fair use.
- A counter-notification is a statement made under penalty of perjury. Knowingly false statements
  create liability under 17 U.S.C. 512(f). The claimant receives your name, address and phone number
  and has about 10 US business days to file a lawsuit before the video is restored.
- A condensed recap of an entire film is unlikely to qualify as fair use: it uses a large part of the
  work and can substitute for watching it. If that describes this video, do not send this; edit or
  remove the video, or obtain a licence instead.
- Not legal advice. Talk to a copyright lawyer (in India, an IP lawyer; for US claims, a US attorney).

FACTS MEASURED FROM THIS CINECUT PROJECT (check each one before relying on it)
{chr(10).join(facts)}

--------------------------------------------------------------------------------
COUNTER-NOTIFICATION (elements required by 17 U.S.C. § 512(g)(3))

1. Material removed and its location before removal:
   [Video title]  [https://www.youtube.com/watch?v=...]

2. Statement under penalty of perjury:
   "I swear, under penalty of perjury, that I have a good faith belief that the material was removed
   or disabled as a result of mistake or misidentification of the material to be removed or disabled."

3. Reason (your own words, only true statements):
   [Explain why the removal was a mistake. If you rely on fair use, explain the commentary or criticism
   you added and why the excerpts were needed for it. Background: 17 U.S.C. § 107 (four factors);
   Campbell v. Acuff-Rose Music, Inc., 510 U.S. 569 (1994); Andy Warhol Foundation v. Goldsmith,
   598 U.S. 508 (2023), which narrowed "transformative" for commercial uses.]

4. Consent to jurisdiction:
   "I consent to the jurisdiction of the Federal District Court for the judicial district in which my
   address is located, or if my address is outside of the United States, for any judicial district in
   which the service provider may be found, and I will accept service of process from the person who
   provided the original notification or an agent of such person."

5. Name, address, telephone number and email:
   [Full legal name]  [Address]  [Phone]  [Email]

6. Signature:
   [Type your full legal name]
================================================================================
"""
