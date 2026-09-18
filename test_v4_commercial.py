import os
import sys
import time
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

from backend.config import FFMPEG_BIN, TEMP_DIR, OUTPUT_DIR
from backend.video_engine.fair_use_shield import enforce_fair_use_clipping, calculate_fair_use_score
from backend.video_engine.season_batch import scan_season_folder, budget_season_episodes, assemble_season_recap
from backend.video_engine.watchfolder import WatchfolderDaemon
from backend.video_engine.renderer import render_summary_video

def create_synthetic_video(output_path: str, duration_sec: int = 15, text: str = "Test Video"):
    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_sec}:size=640x360:rate=24",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_sec}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(output_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

def run_v4_tests():
    print("=" * 65)
    print("🧪 CineCut 4.0 Commercial Features Automated Verification Suite")
    print("=" * 65)

    test_dir = TEMP_DIR / "v4_test_run"
    test_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------
    # 1. YouTube Fair-Use Copyright Shield Tests
    # -------------------------------------------------------------
    print("\n[TEST 1] Testing YouTube Fair-Use Copyright Shield...")
    scenes = [
        {"start": 0.0, "end": 6.5, "duration": 6.5, "title": "Short Take", "selected": True},
        {"start": 10.0, "end": 28.0, "duration": 18.0, "title": "Long Dialogue Scene", "selected": True},
        {"start": 35.0, "end": 40.0, "duration": 5.0, "title": "Action Cut", "selected": True}
    ]

    capped = enforce_fair_use_clipping(scenes, max_clip_sec=7.0)
    for c in capped:
        assert c["duration"] <= 7.01, f"Scene '{c['title']}' duration {c['duration']}s exceeds 7.0s cap!"
    assert len(capped) > len(scenes), "Long scene was not subdivided into micro-takes"
    print(f"  [✓] 18.0s scene was successfully subdivided into {len(capped)-2} transformative micro-takes <= 7.0s")

    # Score calculation test
    score_data = calculate_fair_use_score(capped, has_horizontal_flip=True)
    assert score_data["breakdown"]["visual_points"] == 0 and score_data["over_limit_count"] == 0, "Mirroring must not add points; clips must be capped"
    assert score_data["has_horizontal_flip"] is True
    print(f"  [✓] Fair-Use Defense Score calculated: {score_data['total_score']}% ({score_data['rating']})")

    # Test Renderer with Horizontal Mirror Flip
    synth_video = test_dir / "fair_use_sample.mp4"
    if not synth_video.exists():
        create_synthetic_video(str(synth_video), duration_sec=10, text="Fair Use Sample")

    rendered_flip = render_summary_video(
        source_video_path=str(synth_video),
        scenes=[{"start": 1.0, "end": 5.0, "duration": 4.0, "title": "Capped Scene 1", "selected": True}],
        job_id="v4_test_flip",
        horizontal_flip=True,
        render_mode="cinematic_nvenc"
    )
    assert os.path.exists(rendered_flip), "Rendered horizontally flipped video must exist"
    assert os.path.getsize(rendered_flip) > 5000, "Rendered file too small"
    print(f"  [✓] Successfully rendered Fair-Use Shielded video with -vf hflip: {Path(rendered_flip).name}")

    # -------------------------------------------------------------
    # 2. TV Series Season Batch Engine Tests
    # -------------------------------------------------------------
    print("\n[TEST 2] Testing TV Series Season Batch Engine...")
    season_dir = test_dir / "Season_01"
    season_dir.mkdir(parents=True, exist_ok=True)

    ep1 = season_dir / "Show.S01E01.Pilot.mp4"
    ep2 = season_dir / "Show.S01E02.Rising.mp4"
    ep3 = season_dir / "Show.S01E03.Finale.mp4"

    for ep, title in [(ep1, "Episode 1 Pilot"), (ep2, "Episode 2 Rising"), (ep3, "Episode 3 Finale")]:
        if not ep.exists():
            create_synthetic_video(str(ep), duration_sec=12, text=title)

    # Test folder scanning
    season_info = scan_season_folder(str(season_dir))
    assert season_info["total_episodes"] == 3, f"Expected 3 episodes, got {season_info['total_episodes']}"
    assert season_info["season_num"] == 1, f"Expected season 1, got {season_info['season_num']}"
    assert season_info["episodes"][0]["episode"] == 1
    assert season_info["episodes"][2]["episode"] == 3
    print(f"  [✓] Season Folder Scanned: Found {season_info['total_episodes']} episodes naturally sorted (E01-E03)")

    # Test narrative budgeting
    budgeted = budget_season_episodes(season_info["episodes"], target_season_minutes=0.25) # 15 seconds total
    assert budgeted[0]["weight_pct"] > budgeted[1]["weight_pct"], "Premiere should have higher weight than middle episode"
    assert budgeted[2]["weight_pct"] > budgeted[0]["weight_pct"], "Finale should have highest weight (+25%)"
    print(f"  [✓] Hollywood Narrative Budgeting: Premiere={budgeted[0]['weight_pct']}%, Mid={budgeted[1]['weight_pct']}%, Finale={budgeted[2]['weight_pct']}%")

    # Test season recap assembly
    season_result = assemble_season_recap(
        batch_id="v4_season_test",
        folder_path=str(season_dir),
        episodes=season_info["episodes"],
        target_season_minutes=0.15,
        render_mode="stream_copy"
    )
    assert season_result["success"] is True
    assert os.path.exists(season_result["output_file"]), "Season recap file must exist"
    assert len(season_result["chapters"]) == 3, "Expected 3 episodic chapter markers"
    print(f"  [✓] Unified Season Recap assembled with {len(season_result['chapters'])} episodic chapters: {season_result['filename']}")

    # -------------------------------------------------------------
    # 3. Plex / Jellyfin Watchfolder Daemon Tests
    # -------------------------------------------------------------
    print("\n[TEST 3] Testing Plex / Jellyfin Watchfolder Daemon...")
    watch_dir = test_dir / "Plex_Watch"
    watch_dir.mkdir(parents=True, exist_ok=True)

    daemon = WatchfolderDaemon(
        watch_dir=str(watch_dir),
        target_minutes=1, # 1 min target
        render_mode="stream_copy",
        check_interval_sec=1
    )

    # Drop a movie file into watch directory
    incoming_movie = watch_dir / "Interstellar (2014).mp4"
    if not incoming_movie.exists():
        create_synthetic_video(str(incoming_movie), duration_sec=90, text="Interstellar Full Movie")

    daemon.registry.pop(str(incoming_movie.resolve()), None)

    # First tick: register candidate size
    daemon.scan_once()
    assert str(incoming_movie.resolve()) in daemon._candidate_sizes, "Movie should be registered for write stability check"

    # Advance time slightly to simulate completed write
    time.sleep(3.2)
    processed = daemon.scan_once()
    assert str(incoming_movie.resolve()) in processed, "Movie should have been processed after write stabilization"

    companion_recap = watch_dir / "Interstellar (2014) - Recap.mp4"
    assert companion_recap.exists(), f"Companion recap {companion_recap} was not created!"
    status = daemon.get_status()
    assert status["completed_count"] >= 1, "Daemon status must reflect completed recap"
    print(f"  [✓] Watchfolder Daemon detected new media, stabilized write, and created companion recap: {companion_recap.name}")

    print("\n" + "=" * 65)
    print("🎉 ALL CINTCUT 4.0 COMMERCIAL FEATURES VERIFIED AND PASSING!")
    print("=" * 65)

if __name__ == "__main__":
    run_v4_tests()
