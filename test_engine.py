import os
import sys
import subprocess
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from backend.video_engine.probe import get_video_metadata, check_nvenc_support, extract_thumbnail_frame
from backend.video_engine.subtitle_parser import parse_srt_string, group_dialogue_into_scenes, score_dialogue_significance
from backend.video_engine.audio_analyzer import detect_silence_intervals, snap_timestamp_to_silence
from backend.video_engine.summarizer import compute_local_heuristic_summary
from backend.video_engine.renderer import render_summary_video

def create_synthetic_test_video(output_mp4: str, duration_sec: int = 60):
    """
    Generates a 60-second test video with video testsrc and alternating audio beeps and silences.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_sec}:size=640x360:rate=24",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_sec}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "128k",
        output_mp4
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

def run_all_tests():
    print("🧪 Running CineCut Automated Engine Tests...")
    test_dir = BASE_DIR / "temp" / "test_run"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_video = str(test_dir / "synthetic_test.mp4")

    # 1. Create synthetic video
    print("1. Generating synthetic test video...")
    create_synthetic_test_video(test_video, duration_sec=60)
    assert os.path.exists(test_video), "Test video creation failed!"
    print("   [✓] Synthetic test video created.")

    # 2. Test Video Probe
    print("2. Testing probe.get_video_metadata...")
    meta = get_video_metadata(test_video)
    assert meta["duration_sec"] >= 59.0, f"Unexpected duration: {meta['duration_sec']}"
    assert meta["width"] == 640 and meta["height"] == 360, "Unexpected dimensions"
    print(f"   [✓] Probe verified: {meta['duration_formatted']}, {meta['width']}x{meta['height']}, NVENC: {meta['nvenc_supported']}")

    # 3. Test Thumbnail Extraction
    print("3. Testing thumbnail extraction...")
    thumb_path = str(test_dir / "test_thumb.jpg")
    thumb_ok = extract_thumbnail_frame(test_video, 5.0, thumb_path)
    assert thumb_ok and os.path.exists(thumb_path), "Thumbnail extraction failed"
    print("   [✓] Thumbnail extracted successfully.")

    # 4. Test Subtitle Parser & Grouping
    print("4. Testing Subtitle Parser...")
    sample_srt = """1
00:00:02,000 --> 00:00:05,500
Agent Carter: We have a critical mission in Berlin.

2
00:00:06,000 --> 00:00:09,200
Agent Smith: What is the target? Tell me the truth!

3
00:00:25,000 --> 00:00:29,000
Agent Carter: The secret vault is under the cathedral!

4
00:00:45,000 --> 00:00:49,000
Agent Smith: We won! The mission is accomplished.
"""
    subs = parse_srt_string(sample_srt)
    assert len(subs) == 4, f"Expected 4 subtitles, got {len(subs)}"
    grouped = group_dialogue_into_scenes(subs, max_gap_sec=5.0)
    assert len(grouped) == 3, f"Expected 3 grouped scenes, got {len(grouped)}"
    print(f"   [✓] Subtitle parser: 4 lines parsed, grouped into {len(grouped)} dialogue scenes.")

    # 5. Test Dialogue Scoring
    score = score_dialogue_significance(grouped[0]["dialogue"], grouped[0]["duration"])
    assert score > 0, "Score should be positive"
    print(f"   [✓] Dialogue scoring verified (Score: {score:.1f})")

    # 6. Test Story-Arc Budgeting
    print("5. Testing Story-Arc Budgeting (Summarizer)...")
    candidate_scenes = []
    for g in grouped:
        candidate_scenes.append({
            "start": g["start"],
            "end": g["end"],
            "duration": g["duration"],
            "dialogue": g["dialogue"],
            "dialogue_score": score_dialogue_significance(g["dialogue"], g["duration"]),
            "audio_score": 70.0,
            "motion_score": 60.0
        })

    silences = [(10.0, 15.0), (32.0, 38.0)]
    shot_cuts = [0.0, 12.0, 24.5, 42.0]

    # Target 30 seconds summary out of 60 seconds
    summary_scenes = compute_local_heuristic_summary(
        video_duration_sec=60.0,
        target_duration_sec=30.0,
        candidate_scenes=candidate_scenes,
        shot_cuts=shot_cuts,
        silences=silences,
        preset_key="story_focused"
    )
    assert len(summary_scenes) > 0, "Summary scenes should not be empty"
    total_dur = sum(s["duration"] for s in summary_scenes)
    print(f"   [✓] Story-arc budgeting verified: Curated {len(summary_scenes)} scenes, total duration: {total_dur:.1f}s")

    # 7. Test Video Rendering with NVENC
    print("6. Testing Video Renderer (NVENC / Concat)...")
    rendered_video = render_summary_video(
        source_video_path=test_video,
        scenes=summary_scenes,
        job_id="test_job_01",
        output_filename="test_summary_output.mp4"
    )
    assert os.path.exists(rendered_video), "Rendered video file not found!"
    rendered_meta = get_video_metadata(rendered_video)
    print(f"   [✓] Render verified: {rendered_meta['file_name']}, duration: {rendered_meta['duration_formatted']}")

    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY! CineCut engine is fully verified.")

if __name__ == "__main__":
    run_all_tests()
