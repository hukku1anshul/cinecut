// Fetches watch guides from the local CineCut server (the page itself cannot reach localhost).
const PORTS = [8080, 8081, 8082, 8083];

async function fetchGuide(params) {
  let lastError = "CineCut is not running on this PC (python run.py).";
  for (const port of PORTS) {
    try {
      const res = await fetch(`http://localhost:${port}/api/watchguide?${new URLSearchParams(params)}`);
      const data = await res.json();
      if (res.ok) return { ok: true, data };
      lastError = data.detail || `Server error ${res.status}`;
      break;
    } catch (e) {
      continue;
    }
  }
  return { ok: false, error: lastError };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "cinecut-guide") {
    fetchGuide(msg.params).then(sendResponse);
    return true;
  }
  return false;
});
