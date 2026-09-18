import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app, base_url="http://127.0.0.1:8080")

def test_api_endpoints():
    print("Testing CineCut FastAPI Endpoints...")

    # 1. Health check
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    print(f"   [✓] GET /api/health: {data}")

    # 2. Frontend index.html served
    res = client.get("/")
    assert res.status_code == 200
    assert "CineCut" in res.text
    print("   [✓] GET / (Frontend HTML loaded successfully)")

    # 3. Probe test video
    test_video = str(BASE_DIR / "temp" / "test_run" / "synthetic_test.mp4")
    res = client.post("/api/probe", json={"video_path": test_video})
    assert res.status_code == 200
    probe_data = res.json()
    assert probe_data["success"] is True
    print(f"   [✓] POST /api/probe: {probe_data['metadata']['file_name']}")

    # 4. Trigger Analysis
    res = client.post("/api/analyze", json={
        "video_path": test_video,
        "target_minutes": 1,
        "preset": "story_focused",
        "use_ai_director": False
    })
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    print(f"   [✓] POST /api/analyze: Started Job {job_id}")

    # 5. Check Job Status
    import time
    time.sleep(1.5)
    res = client.get(f"/api/job/{job_id}")
    assert res.status_code == 200
    job_info = res.json()
    print(f"   [✓] GET /api/job/{job_id}: Status = {job_info['status']}")

    print("\n🎉 ALL API ENDPOINTS VERIFIED!")

if __name__ == "__main__":
    test_api_endpoints()
