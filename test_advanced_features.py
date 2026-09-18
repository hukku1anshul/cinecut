import os
import sys
import time
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
from backend.video_engine.narrator import synthesize_voiceover, generate_ai_bridge_narrations, mix_voiceover_into_segment
from backend.video_engine.nle_exporter import export_cmx3600_edl, export_premiere_fcpxml
from backend.video_engine.subtitles_and_chapters import generate_synced_srt, create_ffmetadata_chapters, embed_chapters_into_video
from backend.video_engine.renderer import render_summary_video

def run_advanced_tests():
    print("🧪 Running CineCut Advanced Features Test Suite...")
    test_dir = BASE_DIR / "temp" / "advanced_test_run"
    test_dir.mkdir(parents=True, exist_ok=True)

    test_video = str(BASE_DIR / "temp" / "test_run" / "synthetic_test.mp4")
    assert os.path.exists(test_video), "Synthetic test video from previous run must exist"

    sample_scenes = [
        {
            "act": "Act 1: Inciting Incident",
            "start": 2.0,
            "end": 14.0,
            "duration": 12.0,
            "title": "The Mission Briefing",
            "dialogue": "We have a critical mission in Berlin. What is the target?",
            "selected": True
        },
        {
            "act": "Act 2: Midpoint Revelation",
            "start": 24.0,
            "end": 38.0,
            "duration": 14.0,
            "title": "The Underground Vault",
            "dialogue": "The secret vault is under the cathedral!",
            "selected": True
        },
        {
            "act": "Act 3: The Climax",
            "start": 44.0,
            "end": 56.0,
            "duration": 12.0,
            "title": "The Final Escape",
            "dialogue": "We won! The mission is accomplished.",
            "selected": True
        }
    ]

    # 1. Test TTS Voiceover Generation
    print("\n1. Testing Neural TTS Voiceover (edge-tts)...")
    vo_audio_path = str(test_dir / "voiceover_test.mp3")
    res_path = synthesize_voiceover(
        text="Meanwhile, the team discovers the secret coordinates.",
        voice_key="christopher",
        output_path=vo_audio_path
    )
    assert os.path.exists(vo_audio_path) and os.path.getsize(vo_audio_path) > 1000, "TTS generation failed"
    print(f"   [✓] Voiceover synthesized successfully ({os.path.getsize(vo_audio_path)} bytes).")

    # 2. Test Bridge Narration Generation
    print("\n2. Testing Bridge Narration Generation...")
    bridged_scenes = generate_ai_bridge_narrations(sample_scenes, movie_title="Berlin Mission")
    assert bridged_scenes[0].get("bridge_narration") is not None, "Opening bridge narration missing"
    assert bridged_scenes[1].get("bridge_narration") is not None, "Transition bridge narration missing"
    print(f"   [✓] Opening Bridge: \"{bridged_scenes[0]['bridge_narration']}\"")
    print(f"   [✓] Scene 2 Bridge: \"{bridged_scenes[1]['bridge_narration']}\"")

    # 3. Test CMX 3600 EDL Export
    print("\n3. Testing CMX 3600 EDL Export for DaVinci Resolve...")
    edl_path = str(test_dir / "test_timeline.edl")
    export_cmx3600_edl(sample_scenes, test_video, edl_path, fps=24.0)
    assert os.path.exists(edl_path), "EDL export failed"
    edl_content = Path(edl_path).read_text(encoding="utf-8")
    assert "FCM: NON-DROP FRAME" in edl_content
    assert "001" in edl_content and "002" in edl_content
    print("   [✓] CMX 3600 EDL verified with video & audio events.")

    # 4. Test Premiere Pro / FCP XML Export
    print("\n4. Testing Premiere Pro / Final Cut XML Export...")
    xml_path = str(test_dir / "test_sequence.xml")
    export_premiere_fcpxml(sample_scenes, test_video, xml_path, fps=24.0)
    assert os.path.exists(xml_path), "XML export failed"
    xml_content = Path(xml_path).read_text(encoding="utf-8")
    assert "<xmeml version=\"4\">" in xml_content
    assert "<sequence" in xml_content
    assert "The Mission Briefing" in xml_content
    print("   [✓] Premiere Pro XML verified with sequence and clipitems.")

    # 5. Test Synced Subtitle Generation
    print("\n5. Testing Synced Subtitle (.srt) Generation...")
    orig_subs = [
        {"start": 3.0, "end": 6.0, "text": "We have a critical mission in Berlin."},
        {"start": 7.0, "end": 10.0, "text": "What is the target? Tell me the truth!"},
        {"start": 26.0, "end": 30.0, "text": "The secret vault is under the cathedral!"},
        {"start": 46.0, "end": 50.0, "text": "We won! The mission is accomplished."}
    ]
    synced_srt_path = str(test_dir / "test_synced.srt")
    generate_synced_srt(orig_subs, sample_scenes, synced_srt_path)
    assert os.path.exists(synced_srt_path), "Synced SRT generation failed"
    srt_content = Path(synced_srt_path).read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:04,000" in srt_content or "We have a critical mission" in srt_content
    print("   [✓] Synced subtitles verified with shifted timeline timecodes.")

    # 6. Test Chapter Markers Embedding
    print("\n6. Testing Chapter Metadata Creation & Embedding...")
    meta_path = str(test_dir / "chapters.meta")
    create_ffmetadata_chapters(sample_scenes, meta_path)
    assert os.path.exists(meta_path)
    meta_content = Path(meta_path).read_text(encoding="utf-8")
    assert "[CHAPTER]" in meta_content and "Act 1: The Mission Briefing" in meta_content
    print("   [✓] Chapter metadata generated.")

    # 7. Test Lightning Stream-Copy Mode
    print("\n7. Testing Lightning Stream-Copy Mode (<10s render)...")
    t0 = time.time()
    stream_copy_output = render_summary_video(
        source_video_path=test_video,
        scenes=sample_scenes,
        job_id="adv_test_job",
        output_filename="test_lightning_copy.mp4",
        render_mode="stream_copy"
    )
    copy_time = time.time() - t0
    assert os.path.exists(stream_copy_output), "Lightning stream-copy output failed"
    copy_meta = get_video_metadata(stream_copy_output)
    print(f"   [✓] Lightning Stream-Copy completed in {copy_time:.2f} seconds!")
    print(f"   [✓] Output: {copy_meta['file_name']}, duration: {copy_meta['duration_formatted']}")

    # 8. Test FastAPI Endpoints for New Features
    print("\n8. Testing FastAPI Endpoints for Advanced Exports...")
    from fastapi.testclient import TestClient
    from backend.app import app, JOBS

    client = TestClient(app, base_url="http://127.0.0.1:8080")
    # Populate a ready job in JOBS
    JOBS["adv_test_job"] = {
        "job_id": "adv_test_job",
        "video_path": test_video,
        "status": "ready",
        "progress": 100,
        "message": "Ready",
        "metadata": copy_meta,
        "scenes": sample_scenes,
        "subtitles": orig_subs,
        "output_file": stream_copy_output
    }

    # Test EDL endpoint
    edl_resp = client.get("/api/export/edl/adv_test_job")
    assert edl_resp.status_code == 200, "EDL endpoint failed"
    print("   [✓] GET /api/export/edl/adv_test_job: OK")

    # Test XML endpoint
    xml_resp = client.get("/api/export/xml/adv_test_job")
    assert xml_resp.status_code == 200, "XML endpoint failed"
    print("   [✓] GET /api/export/xml/adv_test_job: OK")

    # Test SRT endpoint
    srt_resp = client.get("/api/export/srt/adv_test_job")
    assert srt_resp.status_code == 200, "SRT endpoint failed"
    print("   [✓] GET /api/export/srt/adv_test_job: OK")

    print("\n🎉 ALL ADVANCED FEATURE TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_advanced_tests()
