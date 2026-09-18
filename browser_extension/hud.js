// CineCut Watch Guide HUD for Netflix / Prime Video (and any page with a <video>).
// It never copies, records or modifies the stream. It only reads the player's position and asks the
// official player to jump to the key scenes from a CineCut watch guide.
// Runs two ways:
//   - Browser extension (browser_extension/): guide fetched by the extension, seeks sent to page_bridge.js
//   - Bookmarklet: loaded from http://localhost:<port>/scrubber.js (often blocked by the site's CSP)
(function () {
  "use strict";
  if (window.__cinecutHud) {
    window.__cinecutHud.toggle();
    return;
  }

  const isExtension = typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id;
  const scriptSrc = (document.currentScript && document.currentScript.src) || "";
  const BASE = scriptSrc.startsWith("http://localhost") || scriptSrc.startsWith("http://127.0.0.1")
    ? new URL(scriptSrc).origin : "http://localhost:8080";

  // ---------- player helpers ----------
  function mainVideo() {
    const vids = Array.from(document.querySelectorAll("video"));
    return vids.sort((a, b) => (b.duration || 0) - (a.duration || 0))[0] || null;
  }

  function seekMainWorld(sec) {
    try {
      const nf = window.netflix && window.netflix.appContext && window.netflix.appContext.state.playerApp.getAPI().videoPlayer;
      if (nf) {
        const ids = nf.getAllPlayerSessionIds();
        const player = nf.getVideoPlayerBySessionId(ids[ids.length - 1]);
        player.seek(Math.round(sec * 1000));
        return;
      }
    } catch (e) { /* fall through */ }
    const v = mainVideo();
    if (v) v.currentTime = sec;
  }

  function seek(sec) {
    if (isExtension) window.postMessage({ cinecut: "seek", sec }, "*");
    else seekMainWorld(sec);
  }

  function detectTitle() {
    const sel = [".atvwebplayersdk-title-text", "[data-automation-id='title']", "h1[data-automation-id='title']",
                 "[data-uia='video-title'] h4", ".video-title h4", "[data-uia='video-title']"];
    for (const s of sel) {
      const el = document.querySelector(s);
      if (el && el.textContent.trim()) return el.textContent.trim();
    }
    return document.title.replace(/\s*[-|]\s*(Prime Video|Netflix).*$/i, "").replace(/^Watch\s+/i, "").trim();
  }

  function fetchGuide(params) {
    if (isExtension) {
      return new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({ type: "cinecut-guide", params }, (res) => {
          if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
          if (!res || !res.ok) return reject(new Error((res && (res.error || (res.data && res.data.detail))) || "CineCut is not running on this PC."));
          resolve(res.data);
        });
      });
    }
    return fetch(`${BASE}/api/watchguide?${new URLSearchParams(params)}`).then(async (r) => {
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Guide request failed");
      return data;
    });
  }

  const fmt = (s) => {
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(x).padStart(2, "0")}`;
  };
  const esc = (t) => String(t || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- UI ----------
  const style = document.createElement("style");
  style.textContent = `
    #cc-hud{position:fixed;top:24px;right:24px;z-index:2147483647;width:340px;max-height:80vh;overflow:auto;
      background:rgba(11,15,25,.96);border:1px solid rgba(99,102,241,.45);border-radius:14px;color:#f8fafc;
      font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;padding:14px;box-shadow:0 12px 36px rgba(0,0,0,.7)}
    #cc-hud *{box-sizing:border-box}
    #cc-hud h3{margin:0;font-size:14px;font-weight:800}
    #cc-hud .row{display:flex;gap:6px;margin-top:8px;align-items:center}
    #cc-hud input,#cc-hud select{flex:1;min-width:0;background:#111827;color:#fff;border:1px solid #334155;border-radius:6px;padding:5px}
    #cc-hud button{background:#6366f1;color:#fff;border:0;border-radius:7px;padding:6px 9px;font-weight:700;cursor:pointer}
    #cc-hud button.sec{background:#1f2937;border:1px solid #334155}
    #cc-hud button.on{background:#10b981}
    #cc-hud ol{padding-left:18px;margin:8px 0 0}
    #cc-hud li{margin:4px 0;cursor:pointer;opacity:.8}
    #cc-hud li.cur{opacity:1;color:#a5b4fc;font-weight:700}
    #cc-hud .note{font-size:11px;color:#94a3b8;margin-top:6px}
    #cc-hud .recap{margin-top:8px;padding:8px;border-radius:8px;background:rgba(99,102,241,.12);font-size:12px}
    #cc-pill{position:fixed;bottom:90px;right:24px;z-index:2147483646;background:#6366f1;color:#fff;border:0;
      border-radius:999px;padding:8px 14px;font:700 13px -apple-system,Segoe UI,sans-serif;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.5)}`;
  document.documentElement.appendChild(style);

  const hud = document.createElement("div");
  hud.id = "cc-hud";
  hud.innerHTML = `
    <div class="row" style="margin-top:0;justify-content:space-between"><h3>🎬 CineCut Watch Guide</h3><button class="sec" id="cc-x">✕</button></div>
    <div class="row"><input id="cc-title" placeholder="Film title"></div>
    <div class="row">
      <input id="cc-runtime" type="number" placeholder="Runtime (min)" title="Runtime in minutes">
      <select id="cc-target"><option value="10">10 min</option><option value="15" selected>15 min</option><option value="20">20 min</option><option value="30">30 min</option></select>
      <select id="cc-lang"><option>English</option><option>Hindi</option></select>
    </div>
    <div class="row"><button id="cc-load">Load guide</button><button class="sec" id="cc-auto">Auto-jump: off</button><button class="sec" id="cc-speak" title="Read recaps aloud">🔈 off</button></div>
    <div class="row"><button class="sec" id="cc-prev">⏮</button><button class="sec" id="cc-next">⏭ Next scene</button>
      <button class="sec" id="cc-minus">-5s</button><button class="sec" id="cc-plus">+5s</button></div>
    <div class="note" id="cc-status">Start the film, then load the guide.</div>
    <div class="recap" id="cc-recap" style="display:none"></div>
    <ol id="cc-list"></ol>
    <div class="note">Only jumps inside the official player. Nothing is copied or downloaded.</div>`;
  const pill = document.createElement("button");
  pill.id = "cc-pill";
  pill.textContent = "🎬 CineCut";
  document.documentElement.appendChild(hud);
  document.documentElement.appendChild(pill);
  hud.style.display = isExtension ? "none" : "block";
  pill.style.display = isExtension ? "block" : "none";

  const $ = (id) => hud.querySelector("#" + id);
  let guide = null, current = -1, auto = false, speak = false, offset = 0, lastSpoken = -1, timer = null;

  function toggle() {
    const show = hud.style.display === "none";
    hud.style.display = show ? "block" : "none";
    pill.style.display = show ? "none" : "block";
    if (show) prefill();
  }
  pill.onclick = toggle;
  $("cc-x").onclick = toggle;

  function prefill() {
    if (!$("cc-title").value) $("cc-title").value = detectTitle();
    const v = mainVideo();
    if (v && v.duration && isFinite(v.duration) && !$("cc-runtime").value) $("cc-runtime").value = Math.round(v.duration / 60);
  }

  function status(msg) { $("cc-status").textContent = msg; }

  function renderList() {
    $("cc-list").innerHTML = (guide.items || []).map((it, i) =>
      `<li data-i="${i}" class="${i === current ? "cur" : ""}">${fmt(it.start_sec + offset)}–${fmt(it.end_sec + offset)} ${esc(it.title)}</li>`).join("");
    $("cc-list").querySelectorAll("li").forEach((li) => { li.onclick = () => go(parseInt(li.dataset.i, 10)); });
  }

  function say(text) {
    if (!speak || !text || !window.speechSynthesis) return;
    const v = mainVideo();
    const prevVol = v ? v.volume : null;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = guide && guide.language === "Hindi" ? "hi-IN" : "en-US";
    if (v) v.volume = Math.min(prevVol, 0.3);
    u.onend = u.onerror = () => { if (v && prevVol !== null) v.volume = prevVol; };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  function go(i) {
    if (!guide || !guide.items.length) return;
    current = Math.max(0, Math.min(i, guide.items.length - 1));
    const it = guide.items[current];
    seek(it.start_sec + offset);
    $("cc-recap").style.display = it.recap ? "block" : "none";
    $("cc-recap").textContent = it.recap || "";
    if (lastSpoken !== current) { lastSpoken = current; say(it.recap); }
    status(`Scene ${current + 1}/${guide.items.length}: ${it.title}`);
    renderList();
  }

  function tick() {
    if (!guide || !auto) return;
    const v = mainVideo();
    if (!v || v.paused) return;
    const t = v.currentTime - offset;
    const items = guide.items;
    const idx = items.findIndex((it) => t >= it.start_sec - 1 && t < it.end_sec);
    if (idx >= 0) {
      if (idx !== current) { current = idx; renderList(); }
      return;
    }
    const next = items.findIndex((it) => it.start_sec > t);
    if (next === -1) {
      auto = false;
      $("cc-auto").textContent = "Auto-jump: off";
      $("cc-auto").classList.remove("on");
      v.pause();
      status("Guide finished. That's the condensed story.");
      return;
    }
    go(next);
  }

  $("cc-load").onclick = async () => {
    prefill();
    const title = $("cc-title").value.trim();
    const runtime = parseFloat($("cc-runtime").value);
    if (!title || !(runtime > 10)) { status("Enter the title and runtime in minutes."); return; }
    status("Building guide...");
    try {
      guide = await fetchGuide({ title, runtime_min: runtime, target_minutes: $("cc-target").value, language: $("cc-lang").value });
      current = -1;
      renderList();
      status(`${guide.items.length} scenes, about ${guide.total_watch_formatted}. ${guide.is_generic ? "Generic positions (film not in database)." : ""}`);
    } catch (e) {
      status("Could not load the guide: " + e.message);
    }
  };
  $("cc-auto").onclick = () => {
    auto = !auto;
    $("cc-auto").textContent = "Auto-jump: " + (auto ? "on" : "off");
    $("cc-auto").classList.toggle("on", auto);
    if (auto && current < 0) go(0);
  };
  $("cc-speak").onclick = () => {
    speak = !speak;
    $("cc-speak").textContent = speak ? "🔊 on" : "🔈 off";
    $("cc-speak").classList.toggle("on", speak);
  };
  $("cc-next").onclick = () => go(current + 1);
  $("cc-prev").onclick = () => go(current - 1);
  $("cc-minus").onclick = () => { offset -= 5; if (guide) renderList(); status(`Offset ${offset}s`); };
  $("cc-plus").onclick = () => { offset += 5; if (guide) renderList(); status(`Offset ${offset}s`); };

  timer = setInterval(tick, 700);
  window.__cinecutHud = { toggle, go, get guide() { return guide; } };
  if (!isExtension) prefill();
})();
