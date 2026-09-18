"""
YouTube embed cut: a shareable condensed version of a YouTube video that copies nothing.

The page plays the ORIGINAL upload through YouTube's official embedded player (allowed by YouTube's
terms), seeking from one kept scene to the next and showing or speaking the gap narration in between.
Views and ads go to the owner, and if the owner turns embedding off the page falls back to timestamped
links on youtube.com.
"""
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

VIDEO_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]")


def youtube_id_from_path(path: str) -> Optional[str]:
    """yt-dlp file names end with '[VIDEO_ID].ext'."""
    m = VIDEO_ID_RE.search(Path(path or "").name)
    return m.group(1) if m else None


def timestamp_links(video_id: str, scenes: List[Dict[str, Any]]) -> List[str]:
    out = []
    for s in sorted([s for s in scenes if s.get("selected", True)], key=lambda s: s["start"]):
        out.append(f"{s.get('start_formatted', '')} {s.get('title', 'Scene')}: https://www.youtube.com/watch?v={video_id}&t={int(s['start'])}s")
    return out


def build_youtube_cut_html(video_id: str, title: str, scenes: List[Dict[str, Any]], language: str = "English") -> str:
    segments = []
    for s in sorted([s for s in scenes if s.get("selected", True)], key=lambda s: s["start"]):
        segments.append({"start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2),
                         "title": s.get("title", "Scene"), "act": s.get("act", ""),
                         "narration": s.get("bridge_narration") or "",
                         "start_formatted": s.get("start_formatted", ""), "end_formatted": s.get("end_formatted", "")})
    total = sum(x["end"] - x["start"] for x in segments)
    data = {"video_id": video_id, "title": title, "segments": segments, "total_sec": round(total),
            "lang": "hi-IN" if language == "Hindi" else "en-US"}
    return (PAGE.replace("__TITLE__", _html_escape(title))
                .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))


def _html_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · condensed cut</title>
<style>
  :root { --bg:#0f1419; --panel:#172029; --ink:#e7edf2; --muted:#93a3b1; --accent:#45b7b9; --rule:#26323c; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 "Segoe UI", Roboto, Arial, sans-serif; }
  main { max-width: 1100px; margin: 0 auto; padding: 22px 18px 40px; display: grid; grid-template-columns: minmax(0,1fr) 320px; gap: 20px; }
  h1 { grid-column: 1 / -1; margin: 0; font-size: 1.35rem; }
  .sub { grid-column: 1 / -1; color: var(--muted); margin: -12px 0 0; font-size: .9rem; }
  .stage { position: relative; aspect-ratio: 16/9; background: #000; border-radius: 10px; overflow: hidden; }
  #player, .stage iframe { position:absolute; inset:0; width:100%; height:100%; }
  .card { position:absolute; inset:0; display:none; place-items:center; background: rgba(8,12,16,.92); padding: 28px; text-align:center; }
  .card.show { display:grid; }
  .card p { font-size: 1.2rem; max-width: 40ch; margin: 0 0 18px; }
  .card small { color: var(--muted); display:block; margin-bottom: 10px; }
  button { background: var(--accent); color: #062021; border: 0; border-radius: 8px; padding: 9px 16px; font-weight: 700; cursor: pointer; }
  button.sec { background: transparent; color: var(--ink); border: 1px solid var(--rule); }
  button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  .controls { display:flex; gap:8px; margin-top:10px; flex-wrap: wrap; align-items:center; }
  .controls label { color: var(--muted); font-size: .88rem; display:flex; gap:6px; align-items:center; }
  aside { background: var(--panel); border: 1px solid var(--rule); border-radius: 10px; padding: 12px; max-height: 70vh; overflow:auto; }
  ol { margin:0; padding-left: 20px; }
  li { padding: 6px 2px; cursor:pointer; color: var(--muted); }
  li.cur { color: var(--ink); font-weight: 700; }
  li span { font-family: Consolas, monospace; font-size: .8rem; color: var(--accent); display:block; }
  .note { grid-column: 1 / -1; color: var(--muted); font-size: .82rem; }
  #fallback { display:none; grid-column: 1 / -1; background: var(--panel); border:1px solid var(--rule); border-radius:10px; padding: 14px; }
  #fallback a { color: var(--accent); }
  @media (max-width: 820px) { main { grid-template-columns: minmax(0,1fr); } }
</style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <p class="sub" id="summary"></p>
  <div>
    <div class="stage">
      <div id="player"></div>
      <div class="card show" id="card"><div><small id="cardLabel">Condensed cut</small><p id="cardText">Plays the original YouTube upload, jumping between the key scenes.</p><button id="cardBtn">Start</button></div></div>
    </div>
    <div class="controls">
      <button class="sec" id="prev">⏮ Previous</button>
      <button class="sec" id="next">Next ⏭</button>
      <label><input type="checkbox" id="speak"> Read narration aloud</label>
      <span id="status" style="color:var(--muted);font-size:.88rem"></span>
    </div>
  </div>
  <aside><ol id="list"></ol></aside>
  <div id="fallback"></div>
  <p class="note">This page plays the official video through YouTube's embedded player; nothing is copied or re-uploaded, and views and ads go to the owner. Made with CineCut.</p>
</main>
<script>
const DATA = __DATA__;
const segs = DATA.segments;
let player = null, idx = 0, timer = null, started = false;
const $ = (id) => document.getElementById(id);
const fmt = (s) => { s = Math.round(s); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60; return (h ? h + ":" + String(m).padStart(2,"0") : m) + ":" + String(x).padStart(2,"0"); };
$("summary").textContent = segs.length + " scenes · about " + fmt(DATA.total_sec) + " instead of the full video";
segs.forEach((s, i) => {
  const li = document.createElement("li");
  const t = document.createElement("span");
  t.textContent = s.start_formatted + " to " + s.end_formatted;
  li.appendChild(t);
  li.appendChild(document.createTextNode(s.title));
  li.addEventListener("click", () => { started = true; showCard(i); });
  $("list").appendChild(li);
});
function highlight() { [...$("list").children].forEach((li, i) => li.classList.toggle("cur", i === idx)); }
function status(t) { $("status").textContent = t; }
function speak(text, done) {
  if (!$("speak").checked || !text || !window.speechSynthesis) { setTimeout(done, text ? 3500 : 300); return; }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = DATA.lang;
  u.onend = u.onerror = () => done();
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}
function showCard(i) {
  idx = Math.max(0, Math.min(i, segs.length - 1));
  clearInterval(timer);
  highlight();
  const s = segs[idx];
  if (player && player.pauseVideo) player.pauseVideo();
  if (!s.narration) { playSegment(); return; }
  $("cardLabel").textContent = "Scene " + (idx + 1) + " of " + segs.length;
  $("cardText").textContent = s.narration;
  $("cardBtn").textContent = "Play scene";
  $("card").classList.add("show");
  speak(s.narration, () => { if ($("card").classList.contains("show") && started) playSegment(); });
}
function playSegment() {
  $("card").classList.remove("show");
  const s = segs[idx];
  player.seekTo(s.start, true);
  player.playVideo();
  status("Scene " + (idx + 1) + "/" + segs.length + ": " + s.title);
  clearInterval(timer);
  timer = setInterval(() => {
    const t = player.getCurrentTime ? player.getCurrentTime() : 0;
    if (t >= segs[idx].end - 0.25) {
      if (idx + 1 < segs.length) showCard(idx + 1);
      else { clearInterval(timer); player.pauseVideo(); status("End of the condensed cut."); }
    }
  }, 250);
}
$("cardBtn").addEventListener("click", () => { started = true; if (player) playSegment(); });
$("next").addEventListener("click", () => { started = true; showCard(idx + 1); });
$("prev").addEventListener("click", () => { started = true; showCard(idx - 1); });
function showFallback(reason) {
  const box = $("fallback");
  box.style.display = "block";
  const p = document.createElement("p");
  p.textContent = reason + " Open each scene on YouTube instead:";
  box.appendChild(p);
  const ol = document.createElement("ol");
  segs.forEach((s) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "https://www.youtube.com/watch?v=" + DATA.video_id + "&t=" + Math.floor(s.start) + "s";
    a.target = "_blank"; a.rel = "noopener";
    a.textContent = s.start_formatted + " " + s.title;
    li.appendChild(a);
    ol.appendChild(li);
  });
  box.appendChild(ol);
}
window.onYouTubeIframeAPIReady = function () {
  player = new YT.Player("player", {
    videoId: DATA.video_id,
    playerVars: { rel: 0, playsinline: 1, modestbranding: 1 },
    events: {
      onReady: () => status("Ready."),
      onError: (e) => { if (e.data === 101 || e.data === 150 || e.data === 153) showFallback("The owner does not allow this video to play on other pages."); else showFallback("The video could not be loaded (error " + e.data + ")."); }
    }
  });
};
const tag = document.createElement("script");
tag.src = "https://www.youtube.com/iframe_api";
tag.onerror = () => showFallback("YouTube could not be reached.");
document.head.appendChild(tag);
</script>
</body>
</html>
"""
