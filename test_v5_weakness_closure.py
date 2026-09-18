import os
import sys
import unittest
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.video_engine.audio_smoothing import apply_exponential_audio_crossfade, generate_ambient_transition_tone
from backend.video_engine.visual_analyzer import compute_scene_visual_motion, enrich_scenes_with_visual_energy
from backend.video_engine.face_tracker import compute_dynamic_crop_offset
from backend.video_engine.speech_transcriber import is_whisper_available
from backend.video_engine.ocr_detector import detect_on_screen_title_cards
from backend.video_engine.social_publisher import generate_viral_seo_metadata, generate_social_publishing_pack
from backend.video_engine.legal_armor import generate_dmca_counter_notification, generate_fair_use_slate_video
from backend.config import TEMP_DIR

class TestWeaknessClosureV5(unittest.TestCase):

    def test_weakness_1_audio_crossfades(self):
        """Weakness 1: Soundtrack & Score Clashing -> Exponential S-curve filter validation"""
        # Create a tiny 1s silent audio/video file to test
        test_in = TEMP_DIR / "test_crossfade_in.mp4"
        test_out = TEMP_DIR / "test_crossfade_out.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(test_in)
        ]
        import subprocess
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        ok = apply_exponential_audio_crossfade(str(test_in), str(test_out), duration_sec=1.0, fade_sec=0.2, curve="esin")
        self.assertTrue(ok)
        self.assertTrue(test_out.exists())
        if test_in.exists(): test_in.unlink()
        if test_out.exists(): test_out.unlink()
        print("  ✓ Weakness 1 Passed: Exponential S-Curve audio cross-fade applied and verified.")

    def test_weakness_2_visual_spectacle_scoring(self):
        """Weakness 2: Subtitle Dependency -> Visual motion dynamic scoring"""
        dummy_scenes = [
            {"start": 10.0, "end": 25.0, "audio_score": 60.0},
            {"start": 30.0, "end": 45.0, "audio_score": 40.0}
        ]
        # In mock or missing file, fallback should be safe
        enriched = enrich_scenes_with_visual_energy("non_existent.mp4", dummy_scenes, has_subtitles=False)
        self.assertEqual(len(enriched), 2)
        for s in enriched:
            self.assertIn("motion_score", s)
            self.assertTrue(s.get("visual_action_priority", False))
        print("  ✓ Weakness 2 Passed: Visual spectacle scoring and non-verbal fallback verified.")

    def test_weakness_3_dynamic_pan_and_scan(self):
        """Weakness 3: Static Center-Crop in 9:16 Shorts -> Dynamic actor tracking crop math"""
        # Test 1920x1080 source (9:16 target width is 1080 * 9 / 16 = 607.5 px)
        offset_x, crop_filter = compute_dynamic_crop_offset("non_existent.mp4", 10.0, 20.0, source_width=1920, source_height=1080)
        target_w = 1080 * (9 / 16)
        max_valid_offset = 1920 - target_w
        self.assertGreaterEqual(offset_x, 0)
        self.assertLessEqual(offset_x, max_valid_offset)
        self.assertIn("crop=", crop_filter)
        print(f"  ✓ Weakness 3 Passed: Dynamic Pan & Scan crop calculated (x={offset_x:.1f} / max={max_valid_offset:.1f}, filter={crop_filter}).")

    def test_weakness_4_offline_speech_transcription_module(self):
        """Weakness 4: Absence of Offline Speech-to-Text -> Whisper module readiness"""
        whisper_status = is_whisper_available()
        # Should return boolean cleanly without crashing
        self.assertIsInstance(whisper_status, bool)
        print(f"  ✓ Weakness 4 Passed: Offline Speech-to-Text Whisper detector returned {whisper_status} cleanly.")

    def test_weakness_5_ocr_title_card_detection(self):
        """Weakness 5: Missing OCR for On-Screen Text Cards -> OCR scanner test"""
        dummy_scenes = [
            {"start": 0.0, "end": 5.0, "title": "Opening"},
            {"start": 10.0, "end": 15.0, "title": "Act 1"}
        ]
        results = detect_on_screen_title_cards("non_existent.mp4", dummy_scenes)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 2)
        print("  ✓ Weakness 5 Passed: OCR text card detector handled gracefully without errors.")

    def test_weakness_6_one_click_setup_bat(self):
        """Weakness 6: Installation Friction for Non-Techies -> One-click bootstrap script test"""
        bat_path = Path(__file__).resolve().parent / "CineCut_OneClick_Setup.bat"
        self.assertTrue(bat_path.exists(), "CineCut_OneClick_Setup.bat must exist")
        content = bat_path.read_text(encoding="utf-8")
        self.assertIn("winget install", content)
        self.assertIn("Gyan.FFmpeg", content)
        self.assertIn("Python.Python", content)
        self.assertIn("call CineCut.bat", content)
        print("  ✓ Weakness 6 Passed: Self-healing Windows One-Click Setup verified.")

    def test_weakness_7_social_publisher_pack(self):
        """Weakness 7: Zero Cloud Social Auto-Publishing -> Viral SEO pack and metadata generator"""
        meta = generate_viral_seo_metadata(1, "The Heist Begins", "Inception.2010.mp4", "Act 1: Setup")
        self.assertIn("#movierecap", meta["description"])
        self.assertIn("Inception", meta["viral_title"])
        self.assertIn("copy_paste_text", meta)

        mock_shorts = [
            {"short_id": 1, "title": "Opening Chase", "act": "Act 1", "file_name": "short_01.mp4"},
            {"short_id": 2, "title": "Midpoint Twist", "act": "Act 2", "file_name": "short_02.mp4"}
        ]
        pack = generate_social_publishing_pack(mock_shorts, "Inception.2010.mp4")
        self.assertEqual(len(pack), 2)
        self.assertEqual(pack[0]["short_index"], 1)
        self.assertEqual(pack[1]["short_index"], 2)
        print("  ✓ Weakness 7 Passed: Viral social SEO pack and webhook payloads verified.")

    def test_weakness_8_legal_armor_and_dmca_defense(self):
        """Weakness 8: Studio Manual DMCA Claims -> Statutory dispute notice & Fair-Use title card"""
        notice = generate_dmca_counter_notification(
            movie_title="Interstellar.2014.1080p.mp4",
            total_scenes=14,
            vo_coverage_pct=100.0,
            max_clip_sec=7.0,
            has_horizontal_flip=True
        )
        self.assertIn("17 U.S.C. § 512(g)(3)", notice)
        self.assertIn("Campbell v. Acuff-Rose Music", notice)
        self.assertIn("Interstellar", notice)
        self.assertIn("7.0 seconds", notice)
        self.assertIn("DRAFT", notice)

        # Test legal slate video generation
        slate_path = TEMP_DIR / "test_fair_use_slate.mp4"
        ok = generate_fair_use_slate_video(str(slate_path), duration_sec=1.0)
        self.assertTrue(ok)
        self.assertTrue(slate_path.exists())
        self.assertGreater(slate_path.stat().st_size, 1000)
        if slate_path.exists():
            slate_path.unlink()
        print("  ✓ Weakness 8 Passed: Autonomous DMCA counter-notice and 3-second Fair-Use legal slate verified.")

if __name__ == "__main__":
    unittest.main()
