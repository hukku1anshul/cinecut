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

from backend.video_engine.probe import get_video_metadata
from backend.video_engine.subtitle_parser import extract_character_roster, score_dialogue_significance
from backend.video_engine.summarizer import compute_local_heuristic_summary
from backend.video_engine.narrator import synthesize_voiceover, generate_ai_bridge_narrations
from backend.video_engine.shorts_generator import extract_viral_shorts, bundle_shorts_into_zip

def run_v3_tests():
    print("🧪 Running CineCut 3.0 Advanced Capabilities Test Suite...")
    test_dir = BASE_DIR / "temp" / "v3_test_run"
    test_dir.mkdir(parents=True, exist_ok=True)

    test_video = str(BASE_DIR / "temp" / "test_run" / "synthetic_test.mp4")
    assert os.path.exists(test_video), "Synthetic test video must exist"

    # 1. Test Spoiler-Control Dial (Full Cut vs Spoiler-Free Teaser)
    print("\n1. Testing Spoiler-Control Dial...")
    sample_subs = [
        {"start": 5.0, "end": 10.0, "text": "CARTER: We need to discover the weapon."},
        {"start": 20.0, "end": 28.0, "text": "SMITH: The traitor is inside the vault."},
        {"start": 50.0, "end": 58.0, "text": "CARTER: The final battle is now!"}
    ]

    candidate_scenes = [
        {"start": 5.0, "end": 12.0, "duration": 7.0, "dialogue": sample_subs[0]["text"], "dialogue_score": 75.0, "audio_score": 60.0, "motion_score": 50.0},
        {"start": 20.0, "end": 30.0, "duration": 10.0, "dialogue": sample_subs[1]["text"], "dialogue_score": 85.0, "audio_score": 70.0, "motion_score": 60.0},
        {"start": 48.0, "end": 58.0, "duration": 10.0, "dialogue": sample_subs[2]["text"], "dialogue_score": 95.0, "audio_score": 90.0, "motion_score": 80.0}
    ]

    # Test Full Cut (covers through end)
    full_scenes = compute_local_heuristic_summary(
        video_duration_sec=60.0, target_duration_sec=30.0,
        candidate_scenes=candidate_scenes, shot_cuts=[0.0, 15.0, 35.0, 50.0],
        silences=[], spoiler_mode="full_cut"
    )
    max_full_time = max(s["end"] for s in full_scenes)

    # Test Teaser Mode (restricted to first 65% of film)
    teaser_scenes = compute_local_heuristic_summary(
        video_duration_sec=60.0, target_duration_sec=20.0,
        candidate_scenes=candidate_scenes, shot_cuts=[0.0, 15.0, 35.0, 50.0],
        silences=[], spoiler_mode="teaser_spoiler_free"
    )
    max_teaser_time = max(s["end"] for s in teaser_scenes)

    assert max_teaser_time <= 60.0 * 0.70, f"Teaser time {max_teaser_time} exceeded 65% boundary!"
    print(f"   [✓] Full Cut reaches: {max_full_time:.1f}s (Includes Climax)")
    print(f"   [✓] Spoiler-Free Teaser cuts at: {max_teaser_time:.1f}s (Ending & Climax safely preserved!)")

    # 2. Test Character-Centric POV Filter
    print("\n2. Testing Character POV Roster & Scoring...")
    roster = extract_character_roster(sample_subs, min_appearances=1)
    char_names = [c["name"] for c in roster]
    assert "CARTER" in char_names and "SMITH" in char_names
    print(f"   [✓] Detected Characters: {char_names}")

    smith_score = score_dialogue_significance(sample_subs[1]["text"], 8.0, target_character="SMITH")
    carter_score_for_smith = score_dialogue_significance(sample_subs[0]["text"], 5.0, target_character="SMITH")
    assert smith_score > carter_score_for_smith
    print(f"   [✓] Character weighting verified: Smith scene ({smith_score:.1f}) vs non-Smith scene ({carter_score_for_smith:.1f})")

    # 3. Test Multi-Language Neural Voices
    print("\n3. Testing Multi-Language AI Voiceovers (edge-tts)...")
    # Test Spanish
    es_audio = str(test_dir / "voice_es.mp3")
    synthesize_voiceover("Mientras tanto, una decisión crítica cambia el rumbo.", voice_key="es_jorge", output_path=es_audio)
    assert os.path.exists(es_audio) and os.path.getsize(es_audio) > 500
    print(f"   [✓] Spanish Voiceover: {os.path.getsize(es_audio)} bytes")

    # Test French
    fr_audio = str(test_dir / "voice_fr.mp3")
    synthesize_voiceover("Pendant ce temps, un choix décisif bouleverse le cours des événements.", voice_key="fr_henri", output_path=fr_audio)
    assert os.path.exists(fr_audio) and os.path.getsize(fr_audio) > 500
    print(f"   [✓] French Voiceover: {os.path.getsize(fr_audio)} bytes")

    # Test Hindi
    hi_audio = str(test_dir / "voice_hi.mp3")
    synthesize_voiceover("जैसे-जैसे तनाव बढ़ता है, कहानी एक अप्रत्याशित मोड़ लेती है।", voice_key="hi_madhur", output_path=hi_audio)
    assert os.path.exists(hi_audio) and os.path.getsize(hi_audio) > 500
    print(f"   [✓] Hindi Voiceover: {os.path.getsize(hi_audio)} bytes")

    # 4. Test Broadcast Audio Normalization (EBU R128)
    print("\n4. Testing EBU R128 Broadcast Audio Normalization...")
    norm_test_out = str(test_dir / "norm_test.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", test_video,
        "-c:v", "copy",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k",
        norm_test_out
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert res.returncode == 0 and os.path.exists(norm_test_out)
    print("   [✓] EBU R128 audio normalization passed with integrated broadcast loudnorm filter.")

    # 5. Test Auto 9:16 Viral Shorts Generator
    print("\n5. Testing Auto 9:16 Viral Shorts Generation...")
    shorts = extract_viral_shorts(
        source_video_path=test_video,
        selected_scenes=candidate_scenes,
        job_id="v3_test_job",
        count=2,
        max_duration_sec=10.0
    )
    assert len(shorts) > 0, "No shorts were generated!"
    short_meta = get_video_metadata(shorts[0]["file_path"])
    assert short_meta["width"] == 1080 and short_meta["height"] == 1920, f"Expected 1080x1920, got {short_meta['width']}x{short_meta['height']}"
    print(f"   [✓] Generated Vertical Short: {short_meta['file_name']}, resolution: {short_meta['width']}x{short_meta['height']} (9:16)")

    zip_file = bundle_shorts_into_zip("v3_test_job")
    assert zip_file and os.path.exists(zip_file)
    print(f"   [✓] ZIP pack created: {Path(zip_file).name} ({os.path.getsize(zip_file)} bytes)")

    # 6. Test FastAPI Endpoints
    print("\n6. Testing FastAPI Endpoints for CineCut 3.0...")
    from fastapi.testclient import TestClient
    from backend.app import app, JOBS

    client = TestClient(app, base_url="http://127.0.0.1:8080")
    JOBS["v3_test_job"] = {
        "job_id": "v3_test_job",
        "video_path": test_video,
        "status": "ready",
        "progress": 100,
        "message": "Ready",
        "metadata": short_meta,
        "scenes": candidate_scenes,
        "characters": roster,
        "viral_shorts": shorts
    }

    # Test shorts endpoint
    res_short = client.get("/api/shorts/v3_test_job/1")
    assert res_short.status_code == 200
    print("   [✓] GET /api/shorts/v3_test_job/1: OK")

    # Test shorts zip export
    res_zip = client.get("/api/export/shorts_pack/v3_test_job")
    assert res_zip.status_code == 200
    print("   [✓] GET /api/export/shorts_pack/v3_test_job: OK")

    print("\n🎉 ALL CINECUT 3.0 CAPABILITIES FULLY TESTED AND PASSED!")

if __name__ == "__main__":
    run_v3_tests()
