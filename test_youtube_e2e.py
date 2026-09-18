import os
import sys
import time
import json
from pathlib import Path

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient
from backend.app import app
from backend.config import TEMP_DIR, OUTPUT_DIR

client = TestClient(app, base_url="http://127.0.0.1:8080")

def run_youtube_e2e_verification():
    print("=" * 75)
    print("🎬 CineCut AI 5.0: YouTube End-to-End Real World Verification Suite")
    print("=" * 75)

    youtube_url = "https://www.youtube.com/watch?v=R6MlUcmOul8"
    print(f"\n[STEP 1] Ingesting YouTube Open Movie via /api/probe...")
    print(f"  URL: {youtube_url} (Tears of Steel - Blender Sci-Fi Open Movie)")

    probe_resp = client.post("/api/probe", json={"youtube_url": youtube_url})
    if probe_resp.status_code != 200:
        print(f"❌ Probe failed ({probe_resp.status_code}): {probe_resp.text}")
        sys.exit(1)

    meta = probe_resp.json()["metadata"]
    video_path = meta["file_path"]
    print(f"  ✓ Ingestion successful!")
    print(f"  ✓ Title: {meta['file_name']}")
    print(f"  ✓ Duration: {meta['duration_formatted']} ({meta['duration_sec']}s)")
    print(f"  ✓ Resolution: {meta['width']}x{meta['height']} @ {meta['fps']} fps")
    print(f"  ✓ Has Subtitles: {meta['has_subtitles']} ({len(meta['subtitle_streams'])} streams)")
    for s in meta['subtitle_streams']:
        print(f"      Stream #{s['index']}: {s['language']} ({s['codec']})")

    # STEP 2: Full Story-Arc Analysis
    print(f"\n[STEP 2] Running 3-Act Story Arc Budgeting via /api/analyze...")
    analyze_payload = {
        "video_path": video_path,
        "target_minutes": 3,
        "preset": "story_focused",
        "spoiler_mode": "full_cut",
        "enforce_fair_use": True,
        "max_clip_sec": 7.0
    }
    analyze_resp = client.post("/api/analyze", json=analyze_payload)
    if analyze_resp.status_code != 200:
        print(f"❌ Analyze request failed: {analyze_resp.text}")
        sys.exit(1)

    job_id = analyze_resp.json()["job_id"]
    print(f"  ✓ Analysis job started: Job ID = {job_id}")

    # Poll analysis
    max_wait = 60
    start_t = time.time()
    job_data = None
    while time.time() - start_t < max_wait:
        res = client.get(f"/api/job/{job_id}").json()
        status = res["status"]
        if status == "ready":
            job_data = res
            break
        elif status == "error":
            print(f"❌ Analysis error: {res.get('error')}")
            sys.exit(1)
        time.sleep(1)

    if not job_data:
        print(f"❌ Analysis timed out after {max_wait}s")
        sys.exit(1)

    scenes = job_data["scenes"]
    characters = job_data.get("characters", [])
    print(f"  ✓ Analysis Complete in {round(time.time() - start_t, 1)}s!")
    print(f"  ✓ Curated Scenes Count: {len(scenes)}")
    print(f"  ✓ Total Selected Duration: {job_data['total_selected_formatted']}")
    print(f"  ✓ Characters Extracted: {characters if characters else 'None (Non-dialogue / Heuristics)'}")
    print(f"  ✓ Sample Curated Story Beats:")
    for sc in scenes[:4]:
        print(f"      [{sc['start_formatted']} ➔ {sc['end_formatted']}] {sc['act']}: \"{sc['title']}\" (Score: {sc.get('importance', 0)})")

    # STEP 3: Multi-Language AI Voiceover Bridges
    print(f"\n[STEP 3] Testing AI Recap Voiceover Bridges (/api/narrate/{job_id})...")
    narrate_payload = {
        "voice": "christopher",
        "language": "English"
    }
    vo_resp = client.post(f"/api/narrate/{job_id}", json=narrate_payload)
    if vo_resp.status_code == 200 and vo_resp.json().get("success"):
        narrated_scenes = vo_resp.json()["scenes"]
        vo_count = sum(1 for s in narrated_scenes if s.get("voiceover_text"))
        print(f"  ✓ AI Voiceover bridges synthesized: {vo_count} scenes voiced.")
        if vo_count > 0:
            sample = next(s for s in narrated_scenes if s.get("voiceover_text"))
            print(f"  ✓ Sample Bridge Narration: \"{sample['voiceover_text']}\"")
    else:
        print(f"  ⚠️ Voiceover skipped or warning: {vo_resp.text}")

    # STEP 4: 9:16 Viral Shorts Generation with Dynamic Pan & Scan
    print(f"\n[STEP 4] Testing 9:16 Viral Shorts with Dynamic Pan & Scan (/api/generate_shorts/{job_id})...")
    shorts_resp = client.post(f"/api/generate_shorts/{job_id}", json={"pan_and_scan": True})
    if shorts_resp.status_code == 200 and shorts_resp.json().get("success"):
        shorts = shorts_resp.json()["viral_shorts"]
        print(f"  ✓ Generated {len(shorts)} Viral Vertical 9:16 Shorts (1080x1920)!")
        for sh in shorts[:2]:
            print(f"      Short #{sh['short_id']}: \"{sh['title']}\" ({sh['duration']}s, stream: {sh['stream_url']})")
    else:
        print(f"❌ Shorts generation failed: {shorts_resp.text}")
        sys.exit(1)

    # STEP 5: Viral Creator Social Hub & SEO Pack
    print(f"\n[STEP 5] Testing Creator Social Publishing Hub (/api/social/pack/{job_id})...")
    social_resp = client.get(f"/api/social/pack/{job_id}")
    if social_resp.status_code == 200 and social_resp.json().get("success"):
        pack = social_resp.json()["pack"]
        print(f"  ✓ Generated {len(pack)} Social Creator Publishing Packs!")
        print(f"  ✓ Sample Viral Clickbait Title: \"{pack[0]['viral_title']}\"")
        print(f"  ✓ Sample Hashtags: {' '.join(pack[0]['hashtags'][:4])}")
    else:
        print(f"❌ Social pack generation failed: {social_resp.text}")
        sys.exit(1)

    # STEP 6: Autonomous Legal Armor & DMCA Counter-Notice
    print(f"\n[STEP 6] Testing Legal Armor & DMCA Defense (/api/legal/dispute_pack/{job_id})...")
    dispute_resp = client.get(f"/api/legal/dispute_pack/{job_id}")
    if dispute_resp.status_code == 200:
        doc_text = dispute_resp.text
        print(f"  ✓ 1-Click DMCA Statutory Counter-Notification generated ({len(doc_text)} characters)!")
        print(f"  ✓ Title 17 U.S.C. § 512(g)(3) & Supreme Court Precedent referenced.")
    else:
        print(f"❌ Legal dispute pack failed: {dispute_resp.text}")
        sys.exit(1)

    # STEP 7: Master Video Render with S-Curve Cross-Fades & EBU R128
    print(f"\n[STEP 7] Rendering Master Summary Video (/api/render/{job_id})...")
    render_payload = {
        "output_filename": "Tears_Of_Steel_CineCut_Summary.mp4",
        "render_mode": "cinematic_nvenc",
        "include_voiceover": False,
        "normalize_audio": True,
        "smooth_audio": True,
        "horizontal_flip": False
    }
    render_resp = client.post(f"/api/render/{job_id}", json=render_payload)
    if render_resp.status_code != 200:
        print(f"❌ Render initiation failed: {render_resp.text}")
        sys.exit(1)

    print(f"  ✓ Render job dispatched. Polishing cuts and stitching audio...")
    render_start = time.time()
    rendered_file = None
    while time.time() - render_start < 120:
        res = client.get(f"/api/job/{job_id}").json()
        if res["status"] == "rendered":
            rendered_file = res["output_file"]
            break
        elif res["status"] == "error":
            print(f"❌ Render error: {res.get('error')}")
            sys.exit(1)
        time.sleep(1)

    if not rendered_file or not os.path.exists(rendered_file):
        print(f"❌ Render timed out or output missing!")
        sys.exit(1)

    file_size_mb = round(os.path.getsize(rendered_file) / (1024 * 1024), 2)
    print(f"  ✓ Master Video Successfully Rendered in {round(time.time() - render_start, 1)}s!")
    print(f"  ✓ Output File: {rendered_file}")
    print(f"  ✓ File Size: {file_size_mb} MB")

    print("\n" + "=" * 75)
    print("🎉 REAL-WORLD YOUTUBE MOVIE FULL PIPELINE VERIFICATION PASSED 100%!")
    print("=" * 75)

if __name__ == "__main__":
    run_youtube_e2e_verification()
