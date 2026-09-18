import math
from pathlib import Path
from typing import List, Dict, Any

def seconds_to_timecode(seconds: float, fps: float = 24.0) -> str:
    """Converts seconds to SMPTE timecode string HH:MM:SS:FF."""
    total_frames = int(round(seconds * fps))
    frames_per_sec = int(round(fps))
    
    ff = total_frames % frames_per_sec
    total_sec = total_frames // frames_per_sec
    
    ss = total_sec % 60
    total_min = total_sec // 60
    
    mm = total_min % 60
    hh = total_min // 60
    
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

def seconds_to_frames(seconds: float, fps: float = 24.0) -> int:
    return int(round(seconds * fps))

def export_cmx3600_edl(
    scenes: List[Dict[str, Any]],
    source_video_path: str,
    output_edl_path: str,
    fps: float = 24.0,
    title: str = "CineCut_Summary"
) -> str:
    """
    Exports a CMX 3600 Edit Decision List (EDL) for DaVinci Resolve, Premiere, and Final Cut.
    """
    source_name = Path(source_video_path).name
    reel_name = (Path(source_video_path).stem[:8]).upper().replace(" ", "_")
    if not reel_name:
        reel_name = "AX"

    lines = [
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        ""
    ]

    record_time_sec = 0.0

    active_scenes = [s for s in scenes if s.get("selected", True)]
    active_scenes.sort(key=lambda x: x["start"])

    for idx, scene in enumerate(active_scenes, start=1):
        src_in = seconds_to_timecode(scene["start"], fps)
        src_out = seconds_to_timecode(scene["end"], fps)
        
        dur = scene["duration"]
        rec_in = seconds_to_timecode(record_time_sec, fps)
        record_time_sec += dur
        rec_out = seconds_to_timecode(record_time_sec, fps)

        # Event line for Video
        lines.append(f"{idx:03d}  {reel_name:<8} V     C        {src_in} {src_out} {rec_in} {rec_out}")
        lines.append(f"* FROM CLIP NAME: {source_name}")
        lines.append(f"* ACT: {scene.get('act', 'General')}")
        lines.append(f"* TITLE: {scene.get('title', 'Scene')}")
        lines.append("")

        # Event line for Audio Track 1 & 2
        lines.append(f"{idx:03d}  {reel_name:<8} AA    C        {src_in} {src_out} {rec_in} {rec_out}")
        lines.append(f"* FROM CLIP NAME: {source_name}")
        lines.append("")

    out_file = Path(output_edl_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("\n".join(lines), encoding="utf-8")
    return str(out_file)

def export_premiere_fcpxml(
    scenes: List[Dict[str, Any]],
    source_video_path: str,
    output_xml_path: str,
    video_width: int = 1920,
    video_height: int = 1080,
    fps: float = 24.0,
    title: str = "CineCut_Summary"
) -> str:
    """
    Exports a Premiere Pro & Final Cut Pro 7 compatible XML sequence (xmeml).
    Allows 1-click importing into Adobe Premiere Pro and DaVinci Resolve with media relinked.
    """
    source_path = Path(source_video_path).resolve()
    source_url = "file://localhost/" + str(source_path).replace("\\", "/")
    source_name = source_path.name
    timebase = int(round(fps))

    active_scenes = [s for s in scenes if s.get("selected", True)]
    active_scenes.sort(key=lambda x: x["start"])

    timeline_frames = 0
    clip_items_xml = []

    for idx, scene in enumerate(active_scenes, start=1):
        in_frame = seconds_to_frames(scene["start"], fps)
        out_frame = seconds_to_frames(scene["end"], fps)
        dur_frames = out_frame - in_frame

        start_frame = timeline_frames
        end_frame = timeline_frames + dur_frames
        timeline_frames = end_frame

        scene_title = scene.get("title", f"Scene_{idx}").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        clip_xml = f"""
					<clipitem id="clipitem-{idx}">
						<name>{scene_title}</name>
						<duration>{dur_frames}</duration>
						<rate>
							<timebase>{timebase}</timebase>
							<ntsc>FALSE</ntsc>
						</rate>
						<in>{in_frame}</in>
						<out>{out_frame}</out>
						<start>{start_frame}</start>
						<end>{end_frame}</end>
						<file id="file-1"/>
					</clipitem>"""
        clip_items_xml.append(clip_xml)

    clips_joined = "".join(clip_items_xml)

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
	<sequence id="sequence-1">
		<name>{title}</name>
		<duration>{timeline_frames}</duration>
		<rate>
			<timebase>{timebase}</timebase>
			<ntsc>FALSE</ntsc>
		</rate>
		<media>
			<video>
				<format>
					<samplecharacteristics>
						<width>{video_width}</width>
						<height>{video_height}</height>
						<rate>
							<timebase>{timebase}</timebase>
							<ntsc>FALSE</ntsc>
						</rate>
					</samplecharacteristics>
				</format>
				<track>
					{clips_joined}
				</track>
			</video>
		</media>
	</sequence>
	<file id="file-1">
		<name>{source_name}</name>
		<pathurl>{source_url}</pathurl>
		<rate>
			<timebase>{timebase}</timebase>
			<ntsc>FALSE</ntsc>
		</rate>
	</file>
</xmeml>
"""
    out_file = Path(output_xml_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(xml_content, encoding="utf-8")
    return str(out_file)
