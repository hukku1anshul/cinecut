"use strict";
// CineCut web app: library, player (watch or listen), creator tools, workspaces, developer keys and plans.

const S = { me: null, lib: { kind: "", q: "", language: "", status: "", offset: 0 }, timers: [] };
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const view = () => document.getElementById("view");
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KIND_NAMES = { movie: "Movie", series: "Series", book: "Book", lecture: "Lecture", story: "Story" };
const STREAM_NAMES = { library: "Library", creator: "Creators", institute: "Institutes and colleges", company: "Company training", api: "Partners and licensing" };
const WORK_NAMES = { shorten: "Short version", creator_cut: "Condensed cut", creator_reels: "Reels", creator_dub: "Dub", creator_explainer: "Own-words explainer",
  explainer: "Book explainer", org_content: "Workspace content" };

async function api(url, opts = {}) {
  const o = { credentials: "same-origin", ...opts };
  if (o.json !== undefined) {
    o.method = o.method || "POST";
    o.headers = { "Content-Type": "application/json" };
    o.body = JSON.stringify(o.json);
    delete o.json;
  }
  const r = await fetch(url, o);
  const type = r.headers.get("content-type") || "";
  const data = type.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    const d = data && data.detail !== undefined ? data.detail : data;
    const err = new Error(typeof d === "string" ? d : JSON.stringify(d));
    err.status = r.status;
    throw err;
  }
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = bad ? "bad" : "";
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 4200);
}

function every(ms, fn) { const id = setInterval(fn, ms); S.timers.push(id); return id; }
const langOptions = (sel = "") => (S.me?.languages || ["English", "Hindi"]).map((l) => `<option ${l === sel ? "selected" : ""}>${esc(l)}</option>`).join("");
const fmtDate = (t) => (t ? new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");

// ---------------------------------------------------------------- account
async function refreshMe() {
  S.me = await api("/api/app/me");
  const who = $("#who");
  if (S.me.user) {
    who.innerHTML = `<a class="btn ghost small" href="#/account">${esc(S.me.user.name)}</a>`;
  } else {
    who.innerHTML = `<button class="btn ghost small" id="loginBtn">Log in</button><button class="btn primary small" id="signupBtn">Sign up free</button>`;
    $("#loginBtn").onclick = () => Auth.open("login");
    $("#signupBtn").onclick = () => Auth.open("signup");
  }
}

const Auth = {
  mode: "signup", resolve: null,
  open(mode = "signup") {
    this.mode = mode;
    const signup = mode === "signup";
    $("#authTitle").textContent = signup ? "Create your free account" : "Welcome back";
    $("#authLede").textContent = signup ? "Everything is free for now." : "Log in to continue.";
    $("#nameRow").hidden = !signup;
    $("#agreeRow").hidden = !signup;
    $("#authSubmit").textContent = signup ? "Create account" : "Log in";
    $("#authSwitch").textContent = signup ? "I already have an account" : "Create a new account";
    $("#authError").hidden = true;
    $("#authDialog").showModal();
    return new Promise((res) => { this.resolve = res; });
  },
  close(ok) {
    $("#authDialog").close();
    if (this.resolve) { this.resolve(ok); this.resolve = null; }
  }
};

async function requireLogin() {
  if (S.me?.user) return true;
  return Auth.open("signup");
}

function initAuth() {
  $("#authSwitch").onclick = () => Auth.open(Auth.mode === "signup" ? "login" : "signup");
  $("#authClose").onclick = () => Auth.close(false);
  $("#authDialog").addEventListener("cancel", () => Auth.close(false));
  $("#authForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = { email: f.get("email"), password: f.get("password") };
    if (Auth.mode === "signup") Object.assign(body, { name: f.get("name") || "", agree: !!f.get("agree") });
    try {
      await api(`/api/app/${Auth.mode === "signup" ? "signup" : "login"}`, { json: body });
      await refreshMe();
      Auth.close(true);
      toast(Auth.mode === "signup" ? "Account created. Everything is free for now." : "Logged in.");
      route();
    } catch (err) {
      $("#authError").textContent = err.message;
      $("#authError").hidden = false;
    }
  });
}

function loginPrompt(title, text) {
  return `<section class="empty"><h2>${esc(title)}</h2><p>${esc(text)}</p><div class="row" style="justify-content:center">
    <button class="btn primary" onclick="Auth.open('signup')">Sign up free</button><button class="btn" onclick="Auth.open('login')">Log in</button></div></section>`;
}

// ---------------------------------------------------------------- router
const routes = { library: pageLibrary, title: pageTitle, play: pagePlay, watch: (id) => pagePlay(id, "video", "", "", true), create: pageCreate, workspace: pageWorkspace,
  developers: pageDevelopers, plans: pagePlans, account: pageAccount };

async function route() {
  Player.stop();
  S.timers.forEach(clearInterval);
  S.timers = [];
  const [name, ...args] = (location.hash.replace(/^#\/?/, "") || "library").split("/");
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.route === name || (name === "title" && a.dataset.route === "library")));
  const page = routes[name] || pageLibrary;
  view().innerHTML = `<p class="muted">Loading…</p>`;
  try {
    await page(...args.map(decodeURIComponent));
  } catch (e) {
    const head = e.status === 451 ? "Not available in your country" : e.status === 404 ? "Not found" : "Something went wrong";
    view().innerHTML = `<section class="empty"><h2>${head}</h2><p>${esc(e.message)}</p>${e.status === 451
      ? `<p><a class="btn" href="#/library">Back to the library</a> <a class="btn ghost" href="#/account">Change your country</a></p>` : ""}</section>`;
  }
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- library
function cardHtml(t, orgId = "") {
  const sub = t.series ? `${t.series}${t.episode ? ` · Episode ${t.episode}` : ""}` : [t.creator, t.year].filter(Boolean).join(" · ");
  return `<a class="card" href="#/title/${esc(t.id)}${orgId ? "/" + esc(orgId) : ""}">
    <div class="cover" style="--h:${Number(t.hue) || 200}">
      <span class="k">${esc(KIND_NAMES[t.kind] || t.kind)}${t.required ? " · required" : ""}</span>
      <span class="t">${esc(t.title)}</span>
      <span class="m">${t.status === "catalog" ? `Original${t.minutes ? " · " + esc(Math.round(t.minutes)) + " min" : ""} · summary on request`
        : `${t.minutes ? esc(t.minutes) + " min summary" : "Summary"}${t.audio_ok ? " · can listen" : ""}`}</span>
    </div>
    <div class="meta"><span class="by">${esc(sub || t.rights?.license || "")}</span>
      <span class="row">${(t.languages || []).map((l) => `<span class="tag">${esc(l)}</span>`).join("")}</span></div></a>`;
}

async function pageLibrary() {
  const st = S.lib;
  const kinds = [["", "All"], ...Object.entries(S.me?.kinds || {})];
  view().innerHTML = `
    <section class="hero">
      <div><span class="tag accent">Free for now</span><h1>Classics, retold in your language</h1>
        <p>Public-domain films, books, stories and lectures, condensed and narrated in Hindi, English and more. Watch them, or just listen.</p></div>
      <form class="panel form" id="reqForm">
        <h3>Ask for a book</h3>
        <p class="muted" style="margin:0">Give a Project Gutenberg book number. If it is public domain in India, CineCut writes its own explainer and adds it here for everyone.</p>
        <div class="two"><label>Book number or link <input name="gid" required placeholder="e.g. 132" /></label>
          <label>Language <select name="language">${langOptions("Hindi")}</select></label></div>
        <div class="two"><label>It is a <select name="kind"><option value="book">Book of ideas</option><option value="story">Story or novel</option></select></label>
          <label>Length <select name="minutes"><option value="5">5 minutes</option><option value="10" selected>10 minutes</option><option value="20">20 minutes</option></select></label></div>
        <button class="btn primary">Make the explainer</button>
        <div id="reqStatus"></div>
      </form>
    </section>
    <section class="filters">
      <div class="chips" id="kinds">${kinds.map(([k, n]) => `<button class="chip ${st.kind === k ? "on" : ""}" data-k="${esc(k)}">${esc(n)}</button>`).join("")}</div>
      <div class="search"><input id="q" type="search" placeholder="Search titles or authors" value="${esc(st.q)}" aria-label="Search" />
        <select id="lang" aria-label="Language"><option value="">All languages</option>${langOptions(st.language)}</select>
        <select id="status" aria-label="Summaries"><option value="">Everything</option>
          <option value="ready" ${st.status === "ready" ? "selected" : ""}>Summaries ready</option>
          <option value="catalog" ${st.status === "catalog" ? "selected" : ""}>Originals (summary on request)</option></select></div>
    </section>
    <p class="muted" id="counts" style="margin:0"></p>
    <section class="grid" id="grid"></section>
    <div class="row" style="justify-content:center"><button class="btn" id="more" hidden>Show more</button></div>`;
  const PLURAL = { movie: "Movies", series: "Series", book: "Books", lecture: "Lectures", story: "Stories" };
  const load = async (append = false) => {
    if (!append) st.offset = 0;
    const d = await api(`/api/app/library?kind=${encodeURIComponent(st.kind)}&q=${encodeURIComponent(st.q)}&language=${encodeURIComponent(st.language)}` +
      `&status=${encodeURIComponent(st.status || "")}&offset=${st.offset || 0}&limit=60`);
    const html = d.titles.map((t) => cardHtml(t)).join("");
    if (append) $("#grid").insertAdjacentHTML("beforeend", html);
    else $("#grid").innerHTML = html || `<div class="empty" style="grid-column:1/-1">Nothing matches. Try another search, or ask for a book above.</div>`;
    st.offset = (st.offset || 0) + d.titles.length;
    $("#more").hidden = st.offset >= d.total;
    const s = d.stats || {};
    $("#counts").textContent = `${d.total.toLocaleString("en-IN")} title(s) match. The library has ` +
      Object.entries(s).map(([k, v]) => `${v.total} ${PLURAL[k] || k} (${v.ready} summarised)`).join(", ") + "." +
      (d.country_name ? ` Showing what is cleared for ${d.country_name}.` : "");
  };
  $("#status").onchange = (e) => { st.status = e.target.value; load(); };
  $("#more").onclick = () => load(true);
  $$("#kinds .chip").forEach((b) => b.onclick = () => { st.kind = b.dataset.k; $$("#kinds .chip").forEach((x) => x.classList.toggle("on", x === b)); load(); });
  let deb;
  $("#q").oninput = (e) => { clearTimeout(deb); deb = setTimeout(() => { st.q = e.target.value; load(); }, 250); };
  $("#lang").onchange = (e) => { st.language = e.target.value; load(); };
  $("#reqForm").onsubmit = async (e) => {
    e.preventDefault();
    if (!(await requireLogin())) return;
    const f = new FormData(e.target);
    const m = String(f.get("gid")).match(/(\d+)/);
    if (!m) return toast("Enter a Project Gutenberg book number.", true);
    try {
      const { work_id } = await api("/api/app/explainers", { json: { gutenberg_id: +m[1], kind: f.get("kind"), language: f.get("language"), minutes: +f.get("minutes") } });
      trackWork(work_id, $("#reqStatus"), (w) => {
        const tid = w.result?.title_id;
        if (tid) { toast("Your explainer is ready."); load(); $("#reqStatus").innerHTML = `<a class="btn teal" href="#/title/${esc(tid)}">Open it</a>`; }
      });
    } catch (err) { toast(err.message, true); }
  };
  await load();
}

function trackWork(wid, box, onDone) {
  const draw = (w) => {
    box.innerHTML = `<div class="bar-progress"><span style="width:${Math.max(3, w.progress || 0)}%"></span></div><p class="muted" style="margin:6px 0 0">${esc(w.message || w.status)}</p>`;
  };
  const id = every(2500, async () => {
    try {
      const w = await api(`/api/app/work/${wid}`);
      draw(w);
      if (w.status === "done") { clearInterval(id); onDone && onDone(w); }
      if (w.status === "error") { clearInterval(id); box.innerHTML = `<p class="error">${esc(w.message)}</p>`; }
    } catch (e) { clearInterval(id); }
  });
  draw({ progress: 2, message: "Starting…" });
}

async function pageTitle(id, orgId = "") {
  const d = await api(`/api/app/titles/${encodeURIComponent(id)}`);
  const t = d.title;
  const r = d.rights || {};
  const langs = t.languages || [];
  view().innerHTML = `
    <section class="detail">
      <div class="cover" style="--h:${Number(t.hue) || 200}"><span class="k">${esc(KIND_NAMES[t.kind] || t.kind)}</span><span class="t">${esc(t.title)}</span>
        <span class="m">${t.minutes ? esc(t.minutes) + " min" : ""}</span></div>
      <div class="panel">
        <div class="row"><span class="tag">${esc(KIND_NAMES[t.kind] || t.kind)}</span>${langs.map((l) => `<span class="tag teal">${esc(l)}</span>`).join("")}</div>
        <h1>${esc(t.title)}</h1>
        <p class="muted" style="margin:0">${esc([t.creator, t.year, t.series].filter(Boolean).join(" · "))}</p>
        ${t.description ? `<p>${esc(t.description)}</p>` : ""}
        ${d.script?.sections?.length ? `<div><h3>What it covers</h3><ol>${d.script.sections.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>` : ""}
        <p class="credit">${esc(r.attribution || t.title)}${r.license ? `<br>${esc(r.license)}` : ""}<br>Summary, narration and voice made with AI (CineCut).${(r.license || "").includes("-SA") ? "<br>This summary is shared under the same licence (CC BY-SA): you may copy and adapt it with credit." : ""}</p>
        <div class="row">
          ${t.status === "catalog" ? `<button class="btn primary" id="requestBtn">Make the summary</button>` : `
            ${langs.length > 1 ? `<select id="lang" class="field" aria-label="Narration language">${langs.map((l) => `<option ${l === d.preferred_language ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>` : ""}
            <button class="btn primary" id="watch">${t.has_video && !d.script ? "▶ Watch the summary" : "▶ Play the summary"}</button>
            ${t.audio_ok ? `<button class="btn teal" id="listen">Listen</button>` : ""}`}
          ${t.watch_original ? `<a class="btn" href="#/watch/${esc(id)}">Watch the original, full length</a>` : ""}
          ${t.page_url && !t.watch_original ? `<a class="btn" href="${esc(t.page_url)}" target="_blank" rel="noopener">Read the original</a>` : ""}
        </div>
        <div id="reqBox"></div>
        ${t.status === "catalog" ? `<p class="muted" style="margin:0">The summary of this title is made when someone asks for it: a few minutes for books and lectures, longer for films.${t.requested ? ` Asked for ${esc(t.requested)} time(s).` : ""}</p>`
          : (t.audio_ok ? "" : `<p class="muted" style="margin:0">Listen mode is not offered for titles that play through YouTube's own player.</p>`)}
        ${d.progress ? `<p class="muted" style="margin:0">You stopped at part ${esc((d.progress.position | 0) + 1)}; playing continues from there.</p>` : ""}
      </div>
    </section>`;
  const go = async (mode) => {
    if (!(await requireLogin())) return;
    const lang = $("#lang") ? $("#lang").value : (langs[0] || "");
    location.hash = `#/play/${encodeURIComponent(id)}/${mode}/${encodeURIComponent(lang)}${orgId ? "/" + orgId : ""}`;
  };
  if ($("#watch")) $("#watch").onclick = () => go("video");
  if ($("#listen")) $("#listen").onclick = () => go("audio");
  if ($("#requestBtn")) $("#requestBtn").onclick = async () => {
    if (!(await requireLogin())) return;
    try {
      const r = await api(`/api/app/titles/${encodeURIComponent(id)}/request`, { method: "POST" });
      $("#reqBox").innerHTML = r.state === "ready" ? `<p>The summary is ready. <a href="#/title/${esc(id)}" onclick="location.reload()">Open it</a>.</p>`
        : `<p class="muted">Added to the queue${r.position ? ` (number ${esc(r.position)})` : ""}. Come back later: the title will show "summary" when it is ready.</p>`;
    } catch (e) { toast(e.message, true); }
  };
}

// ---------------------------------------------------------------- player
const Player = {
  plan: null, mode: "video", i: 0, media: null, narr: null, yt: null, poll: null, saver: null, speed: 1, paused: false, done: false, pre: [], orgId: "",

  count() { return this.plan.mode === "script" ? this.plan.pieces.length : this.plan.clips.length; },
  url(n) { return `/api/app/titles/${this.plan.id}/narration/${encodeURIComponent(this.plan.language)}/${n}`; },

  start(plan, mode, progress, orgId) {
    this.stop();
    this.plan = plan;
    this.orgId = orgId || "";
    this.mode = plan.source?.backend === "youtube" ? "video" : mode;
    this.i = Math.max(0, Math.min((progress && progress.position) | 0, this.count() - 1));
    this.narr = new Audio();
    this.speed = 1;
    this.stage();
    this.session();
    this.saver = setInterval(() => this.save(), 15000);
    if (plan.mode === "script" || plan.source.backend !== "youtube") this.play(this.i);
  },

  stage() {
    const st = $("#stage");
    const b = this.plan.source?.backend;
    $$("#modeSeg button").forEach((x) => x.classList.toggle("on", x.dataset.m === this.mode));
    if (this.plan.mode === "script") {
      st.innerHTML = this.mode === "audio" ? this.listenCard() : `<div class="slide" id="slide"></div><div class="caption" id="cap"></div>`;
      return;
    }
    if (b === "youtube") {
      st.innerHTML = `<div id="ytHost"></div><div class="caption" id="cap"></div>`;
      this.initYouTube();
      return;
    }
    const src = b === "local" ? `/api/app/titles/${this.plan.id}/media` : this.plan.source.url;
    if (this.mode === "audio") {
      this.media = new Audio(src);
      st.innerHTML = this.listenCard();
    } else {
      st.innerHTML = `<video id="vid" playsinline preload="auto"></video><div class="caption" id="cap"></div>`;
      this.media = $("#vid");
      this.media.src = src;
    }
    this.media.addEventListener("timeupdate", () => this.tick());
    this.media.addEventListener("ended", () => { if (!this.done) this.next(); });
    this.media.addEventListener("error", () => toast("The source could not be loaded from its site. Try again later.", true));
  },

  listenCard() {
    return `<div class="listen"><span class="kick">Listening</span><h2 style="color:#fff">${esc(this.plan.title)}</h2><p id="lsub"></p>
      <div class="wave" aria-hidden="true"><i></i><i></i><i></i><i></i></div></div><div class="caption" id="cap"></div>`;
  },

  initYouTube() {
    const go = () => {
      this.yt = new YT.Player("ytHost", {
        videoId: this.plan.source.video_id, playerVars: { controls: 0, rel: 0, playsinline: 1, modestbranding: 1 },
        events: { onReady: () => this.play(this.i), onError: () => toast("YouTube could not play this video here.", true),
          onStateChange: (ev) => { if (ev.data === 0 && !this.done) this.next(); } }
      });
      this.poll = setInterval(() => this.tick(), 250);
    };
    if (window.YT && window.YT.Player) return go();
    window.onYouTubeIframeAPIReady = go;
    if (!document.getElementById("ytapi")) {
      const s = document.createElement("script");
      s.id = "ytapi";
      s.src = "https://www.youtube.com/iframe_api";
      document.head.appendChild(s);
    }
  },

  caption(text) { const c = $("#cap"); if (c) c.textContent = text || ""; },
  setVol(v) {
    if (this.yt && this.yt.setVolume) this.yt.setVolume(Math.round(v * 100));
    else if (this.media) this.media.volume = v;
  },

  speak(n, after) {
    const sub = $("#lsub");
    if (sub) sub.textContent = this.plan.texts[n] || "";      // listen mode shows the words once, in the card
    else this.caption(this.plan.texts[n]);
    this.narr.onended = () => { this.caption(""); after && after(); };
    this.narr.onerror = () => { this.caption(""); after && after(); };
    this.narr.src = this.url(n);
    this.narr.playbackRate = this.speed;
    this.narr.play().catch((e) => { if (e && e.name === "NotAllowedError") this.needTap(); });
  },

  needTap() {
    // Browsers block sound until the page is touched (for example when a player link is opened directly)
    if ($("#tapStart")) return;
    const st = $("#stage");
    const b = document.createElement("button");
    b.id = "tapStart";
    b.className = "btn primary";
    b.style.cssText = "position:absolute;inset:auto;z-index:5";
    b.textContent = "▶ Press to start";
    b.onclick = () => { b.remove(); this.play(this.i); };
    st.appendChild(b);
  },

  prefetch(n) {
    if (n == null || n < 0 || n >= (this.plan.texts || []).length) return;
    const a = new Audio();
    a.preload = "auto";
    a.src = this.url(n);
    this.pre = [a, ...this.pre].slice(0, 3);
  },

  play(i) {
    this.i = i;
    this.done = false;
    this.paused = false;
    $("#pp") && ($("#pp").textContent = "Pause");
    $("#after") && ($("#after").innerHTML = "");
    this.position();
    if (this.plan.mode === "script") {
      const p = this.plan.pieces[i];
      this.slide(p.section);
      this.speak(p.n, () => this.next());
      this.prefetch(this.plan.pieces[i + 1]?.n);
      return;
    }
    const c = this.plan.clips[i];
    if (this.yt && this.yt.seekTo) { this.yt.seekTo(c.start, true); this.yt.setPlaybackRate(this.speed); this.yt.playVideo(); }
    else if (this.media) {
      this.media.currentTime = c.start;
      this.media.playbackRate = this.speed;
      this.media.play().catch((e) => { if (e && e.name === "NotAllowedError") this.needTap(); });
    }
    this.setVol(1);
    if (c.narration != null) { this.setVol(0.22); this.speak(c.narration, () => this.setVol(1)); }
    else this.caption("");
    const nextNarr = this.plan.clips[i + 1]?.narration;
    this.prefetch(nextNarr);
  },

  slide(section) {
    const el = $("#slide");
    const sub = $("#lsub");
    const s = this.plan.slides[section];
    if (sub && !this.plan.texts) sub.textContent = s ? s.heading : "";
    if (!el) return;
    el.innerHTML = section < 0 || !s
      ? `<span class="kick">Own-words summary</span><h2>${esc(this.plan.title)}</h2><p style="color:#cfc7da;max-width:60ch">${esc(this.plan.thesis || "")}</p>`
      : `<span class="kick">${section + 1} / ${this.plan.slides.length}</span><h2>${esc(s.heading)}</h2><ul>${(s.points || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  },

  tick() {
    if (this.done || this.plan?.mode !== "clips") return;
    const t = this.yt && this.yt.getCurrentTime ? this.yt.getCurrentTime() : this.media?.currentTime;
    const c = this.plan.clips[this.i];
    if (c && t >= c.end - 0.08) this.next();
  },

  next() { if (this.i + 1 < this.count()) this.play(this.i + 1); else this.finish(); },
  prev() { this.play(Math.max(0, this.i - 1)); },

  toggle() {
    this.paused = !this.paused;
    const m = this.yt || this.media;
    if (this.paused) {
      this.narr.pause();
      if (this.yt && this.yt.pauseVideo) this.yt.pauseVideo(); else if (this.media) this.media.pause();
    } else {
      if (this.narr.src && !this.narr.ended) this.narr.play().catch(() => {});
      if (this.yt && this.yt.playVideo) this.yt.playVideo(); else if (this.media && this.plan.mode === "clips") this.media.play().catch(() => {});
    }
    $("#pp").textContent = this.paused ? "Play" : "Pause";
    return m;
  },

  setSpeed(v) {
    this.speed = v;
    this.narr.playbackRate = v;
    if (this.media) this.media.playbackRate = v;
    if (this.yt && this.yt.setPlaybackRate) this.yt.setPlaybackRate(v);
  },

  setMode(m) {
    if (m === this.mode) return;
    if (m === "audio" && this.plan.source?.backend === "youtube") return toast("Listen mode is not offered for YouTube titles.", true);
    const i = this.i;
    this.halt();
    this.mode = m;
    this.stage();
    this.play(i);
  },

  position() {
    const el = $("#pos");
    if (el) el.textContent = `${this.plan.mode === "script" ? "Part" : "Scene"} ${this.i + 1} of ${this.count()}`;
  },

  finish() {
    this.done = true;
    this.halt(false);
    const c = this.plan.credits || {};
    $("#stage").innerHTML = `<div class="endcard"><span class="kick">Credits</span><h2>${esc(this.plan.title)}</h2>
      <p>${esc(c.line || "")}</p>${c.license ? `<p>${esc(c.license)}</p>` : ""}<p style="font-size:14px">${esc(c.ai_label || "")}</p>
      <div class="row"><button class="btn primary" id="again">Play again</button></div></div>`;
    $("#again").onclick = () => { this.stage(); this.play(0); };
    this.save(0);
    this.after();
  },

  after() {
    const box = $("#after");
    const p = this.plan;
    if (p.mode !== "script") {
      if (this.orgId) api(`/api/app/orgs/${this.orgId}/titles/${p.id}/complete`, { json: { score: 0, total: 0 } }).then(() => toast("Marked as watched."));
      return;
    }
    box.innerHTML = `<div class="cols">
      <div class="panel"><h3>Takeaways</h3><ul>${(p.takeaways || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>
      <div class="panel"><h3>Check yourself</h3><ol class="quiz">${(p.quiz || []).map((q, k) => `<li>${esc(q.q)}
        <details><summary>Show answer</summary>${esc(q.a)}</details><label class="check"><input type="checkbox" data-k="${k}" /> <span>I got this right</span></label></li>`).join("")}</ol>
        ${this.orgId ? `<button class="btn primary" id="markDone">Mark complete</button>` : ""}</div></div>`;
    if ($("#markDone")) $("#markDone").onclick = async () => {
      const score = $$(".quiz input:checked").length;
      await api(`/api/app/orgs/${this.orgId}/titles/${p.id}/complete`, { json: { score, total: (p.quiz || []).length } });
      toast(`Recorded: ${score} of ${(p.quiz || []).length}.`);
    };
  },

  session() {
    if (!("mediaSession" in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: this.plan.title, artist: this.plan.creator || "CineCut", album: "CineCut library" });
      navigator.mediaSession.setActionHandler("play", () => { if (this.paused) this.toggle(); });
      navigator.mediaSession.setActionHandler("pause", () => { if (!this.paused) this.toggle(); });
      navigator.mediaSession.setActionHandler("nexttrack", () => this.next());
      navigator.mediaSession.setActionHandler("previoustrack", () => this.prev());
    } catch (e) { /* not supported */ }
  },

  save(pos) {
    if (!this.plan) return;
    api(`/api/app/titles/${this.plan.id}/progress`, { json: { position: pos ?? this.i, mode: this.mode } }).catch(() => {});
  },

  halt(keepSave = true) {
    try { this.narr && this.narr.pause(); } catch (e) { /* ignore */ }
    try { if (this.media) { this.media.pause(); this.media.removeAttribute("src"); this.media.load(); } } catch (e) { /* ignore */ }
    try { if (this.yt && this.yt.destroy) this.yt.destroy(); } catch (e) { /* ignore */ }
    clearInterval(this.poll);
    this.media = null;
    this.yt = null;
    if (keepSave && this.plan && !this.done) this.save();
  },

  stop() {
    if (!this.plan) return;
    this.halt();
    clearInterval(this.saver);
    this.plan = null;
  }
};

async function pagePlay(id, mode = "video", lang = "", orgId = "", original = false) {
  if (!(await requireLogin())) { location.hash = `#/title/${id}`; return; }
  const d = await api(`/api/app/titles/${encodeURIComponent(id)}?lang=${encodeURIComponent(lang)}${original ? "&original=1" : ""}`);
  const p = d.plan;
  if (p.mode === "none") throw new Error("This title's summary is not made yet. Open the title and press Make the summary.");
  view().innerHTML = `
    <section class="row" style="justify-content:space-between">
      <div><a class="muted" href="#/title/${esc(id)}${orgId ? "/" + esc(orgId) : ""}">← Back to the title</a><h2>${esc(p.title)}</h2></div>
      <div class="seg" id="modeSeg" role="group" aria-label="Watch or listen"><button data-m="video">Watch</button><button data-m="audio">Listen</button></div>
    </section>
    <section class="stage" id="stage" aria-live="polite"></section>
    <section class="controls">
      <div class="row"><button class="btn" id="prev" aria-label="Previous">⏮</button><button class="btn primary" id="pp">Pause</button>
        <button class="btn" id="next" aria-label="Next">⏭</button><span class="muted" id="pos"></span></div>
      <label class="row muted">Speed <select id="speed" class="field"><option value="0.75">0.75×</option><option value="1" selected>1×</option>
        <option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>
    </section>
    <p class="muted" style="margin:0">${esc(p.credits?.line || "")} · ${esc(p.credits?.ai_label || "")}</p>
    <section id="after"></section>`;
  $("#prev").onclick = () => Player.prev();
  $("#next").onclick = () => Player.next();
  $("#pp").onclick = () => Player.toggle();
  $("#speed").onchange = (e) => Player.setSpeed(+e.target.value);
  $$("#modeSeg button").forEach((b) => b.onclick = () => Player.setMode(b.dataset.m));
  if (!p.texts?.length && p.mode === "script") throw new Error("This title has no narration in that language.");
  Player.start(p, mode === "audio" ? "audio" : "video", d.progress, orgId);
}

// ---------------------------------------------------------------- uploads and work lists
function upload(file, extra, bar) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(extra).forEach(([k, v]) => v != null && fd.append(k, v));
    const x = new XMLHttpRequest();
    x.open("POST", "/api/app/uploads");
    x.upload.onprogress = (e) => { if (e.lengthComputable && bar) bar.style.width = `${Math.round((e.loaded / e.total) * 100)}%`; };
    x.onload = () => {
      let d = {};
      try { d = JSON.parse(x.responseText); } catch (e) { /* ignore */ }
      if (x.status >= 200 && x.status < 300) resolve(d); else reject(new Error(d.detail || `Upload failed (${x.status}).`));
    };
    x.onerror = () => reject(new Error("Upload failed. Check the connection."));
    x.send(fd);
  });
}

function workHtml(w) {
  if (w.kind === "shorten") return shortHtml(w);
  const files = (w.files || []).map((f) => {
    const url = `/api/app/work/${w.id}/file/${encodeURIComponent(f)}`;
    return /video|reel/.test(f)
      ? `<div><video src="${url}" controls preload="metadata"></video><a class="btn small" href="${url}?download=true">Download ${esc(f)}</a></div>`
      : `<div><a class="btn small" href="${url}?download=true">Download ${esc(f)}</a></div>`;
  }).join("");
  const titles = (w.result?.title_ids || []).map((t) => `<a class="btn small teal" href="#/title/${esc(t)}${w.org_id ? "/" + w.org_id : ""}">Open</a>`).join("");
  return `<div class="work"><div class="row" style="justify-content:space-between"><strong>${esc(WORK_NAMES[w.kind] || w.kind)} · ${esc(w.params?.name || "")}</strong>
      <span class="tag ${w.status === "done" ? "teal" : w.status === "error" ? "" : "accent"}">${esc(w.status)}</span></div>
    ${w.status === "running" || w.status === "queued" ? `<div class="bar-progress"><span style="width:${Math.max(3, w.progress || 0)}%"></span></div>` : ""}
    <p class="muted" style="margin:0">${esc(w.message || "")}</p>${files ? `<div class="files">${files}</div>` : ""}${titles ? `<div class="row">${titles}</div>` : ""}</div>`;
}

async function listWork(box, orgId) {
  const draw = async () => {
    const d = await api(`/api/app/work${orgId ? `?org_id=${orgId}` : ""}`);
    if (!d.work.length) { box.innerHTML = `<p class="muted">Nothing yet.</p>`; return false; }
    box.querySelector(":scope > p.muted")?.remove();
    const keep = new Set();
    d.work.forEach((w, i) => {
      keep.add(w.id);
      let el = box.querySelector(`:scope > [data-wid="${w.id}"]`);
      if (!el) { el = document.createElement("div"); el.dataset.wid = w.id; box.insertBefore(el, box.children[i] || null); }
      const sig = [w.status, w.progress, w.message, w.watchable, (w.files || []).join()].join("|");
      if (el.dataset.sig === sig) return;
      el.dataset.sig = sig;
      el.innerHTML = workHtml(w);
      el.querySelectorAll("[data-sdel]").forEach((b) => b.onclick = async () => {
        if (!confirm("Delete this short version now?")) return;
        try { await api(`/api/app/shorten/${b.dataset.sdel}`, { method: "DELETE" }); toast("Deleted."); draw(); } catch (e) { toast(e.message, true); }
      });
    });
    [...box.children].forEach((el) => { if (el.dataset.wid && !keep.has(el.dataset.wid)) el.remove(); });
    return d.work.some((w) => w.status === "running" || w.status === "queued");
  };
  let busy = await draw();
  const id = every(3000, async () => { if (busy) busy = await draw().catch(() => false); else clearInterval(id); });
  return () => { busy = true; };
}

// ---------------------------------------------------------------- the uploader agreement
let AGREEMENT = null;

async function agreement() {
  if (!AGREEMENT) AGREEMENT = await api("/api/app/agreement");
  return AGREEMENT;
}

function agreeHtml(a, p) {
  const bases = Object.entries(a.bases).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  return `<div class="agree">
    <details><summary>Uploader Agreement, version ${esc(a.version)}: what you promise about this file, and what we do with it</summary>
      <pre class="agree-text">${esc(a.text)}</pre></details>
    <label>This file is mine to give because <select id="${p}Basis">${bases}</select></label>
    <label id="${p}DetailRow" hidden><span id="${p}DetailLabel"></span><input id="${p}Details" maxlength="200" /></label>
    <label class="check"><input type="checkbox" id="${p}Agree" /> <span>I accept the Uploader Agreement for this file. My account, the time, the choice above and a
      fingerprint of the file are recorded, and I can download that record.</span></label></div>`;
}

function wireAgree(p) {
  const sel = $(`#${p}Basis`), row = $(`#${p}DetailRow`), lab = $(`#${p}DetailLabel`);
  const sync = () => { const need = AGREEMENT.details[sel.value]; row.hidden = !need; lab.textContent = need || ""; };
  sel.onchange = sync;
  sync();
}

function agreeValues(p) {
  if (!$(`#${p}Agree`).checked) throw new Error("Read the Uploader Agreement and accept it to upload this file.");
  const basis = $(`#${p}Basis`).value, details = ($(`#${p}Details`)?.value || "").trim();
  if (AGREEMENT.details[basis] && !details) throw new Error(`${AGREEMENT.details[basis]}: please fill this in.`);
  return { basis, details, agreed_hash: AGREEMENT.hash };
}

// ---------------------------------------------------------------- shorten a link (nothing is kept)
async function pageCreate(shared = "") {
  if (!S.me?.user) { view().innerHTML = loginPrompt("Any video, shortened for you", "Sign up free, paste a link and watch a short version made for you. Nothing is kept."); return; }
  const [T, A] = await Promise.all([api("/api/app/shorten/terms"), agreement()]);
  view().innerHTML = `
    <section class="hero"><div><span class="tag teal">Shorten</span><h1>Paste a link. Watch the short version.</h1>
      <p>CineCut keeps the parts you care about, adds a short narration between them if you like, and plays the result here.
      The original is deleted as soon as the short version is ready. The short version can't be downloaded, and it is deleted
      ${T.keep_minutes} minutes after you last watch it.</p></div></section>
    <section class="cols">
      <form class="panel form" id="sForm">
        <h3>1. The video</h3>
        <div class="row" role="radiogroup" aria-label="Where the video is">
          <label class="check"><input type="radio" name="sSrc" value="link" checked /> <span>A link</span></label>
          <label class="check"><input type="radio" name="sSrc" value="file" /> <span>My own video file</span></label></div>
        <input id="sUrl" type="url" class="field" placeholder="https://www.youtube.com/watch?v=..." value="${esc(shared)}" />
        <div id="sFileBox" hidden><input type="file" id="sFile" accept="video/*" class="field" />${agreeHtml(A, "c")}
          <div class="bar-progress"><span id="sBar"></span></div></div>
        <h3>2. How short, and what to keep</h3>
        <div class="two"><label>Length (minutes) <input id="sMin" type="number" min="1" max="60" value="10" /></label>
          <label>It is mostly <select id="sStyle"><option value="movie">A film, story or vlog</option><option value="lecture">A talk or lesson</option></select></label></div>
        <label>Keep mostly <select id="sPreset">${Object.entries(T.presets).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select></label>
        <div class="two"><label class="check"><input type="checkbox" id="sNarr" checked /> <span>Add a short narration between the parts</span></label>
          <label>Narration language <select id="sLang">${langOptions("Hindi")}</select></label></div>
        <div class="agree"><details><summary>How this works, please read</summary><pre class="agree-text">${esc(T.text)}</pre></details>
          <label class="check"><input type="checkbox" id="sAgree" /> <span>I have read how this works and I accept it.</span></label></div>
        <button class="btn primary">Shorten and watch</button>
        <p class="error" id="sErr" hidden></p>
      </form>
      <div class="panel"><h3>Your short versions</h3><div id="sWork"></div></div>
    </section>`;
  wireAgree("c");
  const fileMode = () => $("input[name=sSrc]:checked").value === "file";
  $$("input[name=sSrc]").forEach((r) => r.onchange = () => { $("#sUrl").hidden = fileMode(); $("#sFileBox").hidden = !fileMode(); });
  const poke = await listWork($("#sWork"));
  $("#sForm").onsubmit = async (e) => {
    e.preventDefault();
    $("#sErr").hidden = true;
    try {
      if (!$("#sAgree").checked) throw new Error("Read how this works and accept it first.");
      let source = { url: $("#sUrl").value.trim() };
      if (fileMode()) {
        const file = $("#sFile").files[0];
        if (!file) throw new Error("Choose a video file.");
        source = { upload_id: (await upload(file, agreeValues("c"), $("#sBar"))).upload_id };
      } else if (!source.url) throw new Error("Paste a link to the video.");
      await api("/api/app/shorten", { json: { ...source, minutes: +$("#sMin").value || 10, style: $("#sStyle").value,
        preset: $("#sPreset").value, narrate: $("#sNarr").checked, language: $("#sLang").value, agreed_hash: T.hash } });
      toast("Started. It appears on the right when it is ready.");
      poke();
      listWork($("#sWork"));
    } catch (err) { $("#sErr").textContent = err.message; $("#sErr").hidden = false; }
  };
}

function shortHtml(w) {
  const name = esc(w.result?.title || w.params?.name || w.params?.url || "");
  if (w.status === "running" || w.status === "queued") {
    return `<div class="work"><strong>${name || "Shortening..."}</strong><div class="bar-progress"><span style="width:${Math.max(3, w.progress || 0)}%"></span></div>
      <p class="muted" style="margin:0">${esc(w.message || "")}</p></div>`;
  }
  if (w.status === "error") return `<div class="work"><strong>${name}</strong><span class="tag">not made</span><p class="muted" style="margin:0">${esc(w.message || "")}</p></div>`;
  if (w.status === "done" && w.watchable) {
    return `<div class="work"><div class="row" style="justify-content:space-between"><strong>${name}</strong>
        <span class="tag teal">${w.result?.short_minutes ?? ""} min (from ${w.result?.source_minutes ?? "?"})</span></div>
      <video src="/api/app/shorten/${esc(w.id)}/watch" controls controlslist="nodownload noremoteplayback" disablepictureinpicture
        oncontextmenu="return false" preload="metadata"></video>
      <div class="row" style="justify-content:space-between"><span class="muted">Only you can watch it. Deleted ${w.result?.keep_minutes || 60} minutes after you last watch it.</span>
        <button class="btn small" data-sdel="${esc(w.id)}">Delete now</button></div></div>`;
  }
  return `<div class="work"><strong>${name}</strong> <span class="tag">deleted</span><p class="muted" style="margin:0">The short version was deleted, as promised. Shorten the link again to watch it.</p></div>`;
}

// ---------------------------------------------------------------- workspaces
async function pageWorkspace(orgId) {
  if (!S.me?.user) { view().innerHTML = loginPrompt("Workspaces for institutes and companies", "Sign up free to share lectures and training with your students or team."); return; }
  const orgs = S.me.orgs || [];
  if (!orgId && orgs.length) { location.hash = `#/workspace/${orgs[0].id}`; return; }
  if (!orgId || orgId === "new") {
    view().innerHTML = `
      <section class="hero"><div><span class="tag accent">Institutes and companies</span><h1>Your lectures and training, in every language your people speak</h1>
        <p>Upload your own lecture recordings, training videos or manuals. CineCut makes study cuts and own-words explainers with quizzes, shared only with your members.</p></div></section>
      <section class="cols">
        <form class="panel form" id="newOrg"><h3>Create a workspace</h3>
          <label>Name <input name="name" required placeholder="e.g. Sunrise Coaching Centre" /></label>
          <label>It is a <select name="kind"><option value="institute">Coaching institute or college</option><option value="company">Company</option></select></label>
          <label>Languages <input name="languages" value="English,Hindi" /></label>
          <button class="btn primary">Create</button></form>
        <form class="panel form" id="joinOrg"><h3>Join with an invite code</h3>
          <label>Invite code <input name="code" required placeholder="e.g. K3F9QX2A" /></label><button class="btn">Join</button></form>
      </section>`;
    $("#newOrg").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try { const d = await api("/api/app/orgs", { json: Object.fromEntries(f) }); await refreshMe(); location.hash = `#/workspace/${d.org.id}`; }
      catch (err) { toast(err.message, true); }
    };
    $("#joinOrg").onsubmit = async (e) => {
      e.preventDefault();
      try { const d = await api("/api/app/orgs/join", { json: { code: new FormData(e.target).get("code") } }); await refreshMe(); location.hash = `#/workspace/${d.org.id}`; }
      catch (err) { toast(err.message, true); }
    };
    return;
  }
  const d = await api(`/api/app/orgs/${orgId}`);
  const admin = d.role === "owner" || d.role === "admin";
  const A = admin ? await agreement() : null;
  const done = d.my_completions || {};
  view().innerHTML = `
    <section class="row" style="justify-content:space-between">
      <div><span class="tag accent">${d.org.kind === "company" ? "Company" : "Institute"} · ${esc(d.role)}</span><h1>${esc(d.org.name)}</h1></div>
      <div class="row"><select id="orgPick" class="field" aria-label="Workspace">${orgs.map((o) => `<option value="${o.id}" ${String(o.id) === String(orgId) ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select>
        <a class="btn small" href="#/workspace/new">New or join</a></div>
    </section>
    <section><h2>Shelf</h2><p class="muted">${d.titles.length} title(s). ${Object.keys(done).length} completed by you.</p>
      <div class="grid">${d.titles.map((t) => cardHtml(t, orgId)).join("") || `<div class="empty" style="grid-column:1/-1">No titles yet.${admin ? " Add a lecture, training video or manual below." : ""}</div>`}</div></section>
    ${admin ? `
    <section class="cols">
      <form class="panel form" id="addForm"><h3>Add content</h3>
        <input type="file" id="aFile" accept="video/*,.txt,.md,.pdf,.srt,.vtt" required class="field" />
        ${agreeHtml(A, "a")}
        <div class="bar-progress"><span id="aBar"></span></div>
        <div class="two"><label>Title <input id="aTitle" placeholder="Optional" /></label><label>Language <select id="aLang">${langOptions("Hindi")}</select></label></div>
        <div class="two"><label>For videos, make <select id="aMake"><option value="both">Study cut and explainer</option><option value="study">Study cut only</option>
          <option value="explainer">Explainer only</option></select></label><label>Minutes <input id="aMin" type="number" min="2" max="40" value="10" /></label></div>
        <label class="check"><input type="checkbox" id="aReq" /> <span>Required for everyone (shows in the completion report)</span></label>
        <button class="btn primary">Upload and make</button><div id="aWork"></div></form>
      <div class="panel"><h3>Invite members</h3><p>Share this code. People sign up free and join with it.</p><p><code style="font-size:22px">${esc(d.invite_code)}</code></p>
        <h3>Members (${d.members.length})</h3><div class="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr></thead><tbody>
        ${d.members.map((m) => `<tr><td>${esc(m.name || "")}</td><td>${esc(m.email)}</td><td>${esc(m.role)}</td><td>${m.role === "owner" ? "" :
          `<button class="btn small" data-role="${m.id}" data-r="${m.role === "admin" ? "member" : "admin"}">${m.role === "admin" ? "Make member" : "Make admin"}</button>
           <button class="btn small ghost" data-rm="${m.id}">Remove</button>`}</td></tr>`).join("")}</tbody></table></div></div>
    </section>
    <section class="panel"><div class="row" style="justify-content:space-between"><h3>Completion report</h3><span class="muted" id="rate"></span></div><div class="table-wrap" id="report"></div></section>
    <section class="panel"><h3>Work in progress</h3><div id="oWork"></div></section>` : ""}`;
  $("#orgPick").onchange = (e) => { location.hash = `#/workspace/${e.target.value}`; };
  if (!admin) return;
  wireAgree("a");
  const poke = await listWork($("#oWork"), orgId);
  $$("[data-role]").forEach((b) => b.onclick = async () => { await api(`/api/app/orgs/${orgId}/members/${b.dataset.role}`, { json: { role: b.dataset.r } }); route(); });
  $$("[data-rm]").forEach((b) => b.onclick = async () => {
    if (!confirm("Remove this member from the workspace?")) return;
    await api(`/api/app/orgs/${orgId}/members/${b.dataset.rm}`, { method: "DELETE" });
    route();
  });
  const rep = await api(`/api/app/orgs/${orgId}/report`);
  $("#rate").textContent = `${rep.completion_rate}% of member × title pairs completed`;
  $("#report").innerHTML = rep.completions.length
    ? `<table><thead><tr><th>Member</th><th>Title</th><th>Score</th><th>When</th></tr></thead><tbody>${rep.completions.map((c) => {
        const t = rep.titles.find((x) => x.id === c.title_id);
        return `<tr><td>${esc(c.name || c.email)}</td><td>${esc(t ? t.title : c.title_id)}</td><td>${c.total ? `${c.score} / ${c.total}` : "watched"}</td><td>${fmtDate(c.completed)}</td></tr>`;
      }).join("")}</tbody></table>`
    : `<p class="muted">No completions yet.</p>`;
  $("#addForm").onsubmit = async (e) => {
    e.preventDefault();
    const file = $("#aFile").files[0];
    if (!file) return;
    try {
      const up = await upload(file, { ...agreeValues("a"), org_id: orgId }, $("#aBar"));
      await api(`/api/app/orgs/${orgId}/content`, { json: { upload_id: up.upload_id, title: $("#aTitle").value, language: $("#aLang").value,
        make: $("#aMake").value, minutes: +$("#aMin").value || 10, required: $("#aReq").checked } });
      toast("Working on it. It appears on the shelf when ready.");
      poke();
      listWork($("#oWork"), orgId);
    } catch (err) { toast(err.message, true); }
  };
}

// ---------------------------------------------------------------- developers
async function pageDevelopers() {
  if (!S.me?.user) { view().innerHTML = loginPrompt("Partner API and licensing", "Sign up free to get API keys for the catalogue, recipes and narration audio."); return; }
  const [keys, usage] = await Promise.all([api("/api/app/api-keys"), api("/api/app/api-usage")]);
  const host = location.origin;
  view().innerHTML = `
    <section class="hero"><div><span class="tag accent">Partners</span><h1>License the library, or build on it</h1>
      <p>Audio platforms and apps can list the catalogue with its credits and licences, fetch play recipes and narration audio, and ask for new explainers of public-domain books.
      Every call is counted per title for revenue share.</p></div></section>
    <section class="cols">
      <div class="panel"><h3>API keys</h3>
        <form class="row" id="keyForm"><input name="name" class="field" placeholder="Key name, e.g. Kuku FM pilot" /><button class="btn primary">Create key</button></form>
        <div id="newKey"></div>
        <div class="table-wrap"><table><thead><tr><th>Name</th><th>Starts with</th><th>Created</th><th>Last used</th><th></th></tr></thead><tbody>
        ${keys.keys.map((k) => `<tr><td>${esc(k.name)}</td><td><code>${esc(k.prefix)}…</code></td><td>${fmtDate(k.created)}</td><td>${fmtDate(k.last_used) || "never"}</td>
          <td>${k.revoked ? "revoked" : `<button class="btn small ghost" data-revoke="${esc(k.id)}">Revoke</button>`}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No keys yet.</td></tr>`}</tbody></table></div></div>
      <div class="panel"><h3>Quick start</h3>
        <pre class="code">curl -H "X-API-Key: YOUR_KEY" ${esc(host)}/api/v1/catalog
curl -H "X-API-Key: YOUR_KEY" ${esc(host)}/api/v1/titles/TITLE_ID
curl -H "X-API-Key: YOUR_KEY" -o part0.mp3 \\
  ${esc(host)}/api/v1/titles/TITLE_ID/narration/Hindi/0
curl -X POST -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \\
  -d '{"gutenberg_id": 132, "language": "Hindi"}' ${esc(host)}/api/v1/explainers</pre>
        <p class="muted" style="margin:0">Limits: 120 calls a minute per key. Every title must keep its credit line and the made-with-AI label wherever it is shown.</p></div>
    </section>
    <section class="panel"><h3>Usage, last 30 days</h3><div class="table-wrap"><table><thead><tr><th>Key</th><th>Endpoint</th><th>Title</th><th>Calls</th></tr></thead><tbody>
      ${usage.usage.map((u) => `<tr><td>${esc(u.key_name)}</td><td>${esc(u.endpoint)}</td><td>${esc(u.title_id || "")}</td><td>${u.calls}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No calls yet.</td></tr>`}
    </tbody></table></div></section>`;
  $("#keyForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const k = await api("/api/app/api-keys", { json: { name: new FormData(e.target).get("name") || "API key" } });
      $("#newKey").innerHTML = `<p>Copy this key now; it is not shown again.</p><p><code>${esc(k.key)}</code></p>`;
    } catch (err) { toast(err.message, true); }
  };
  $$("[data-revoke]").forEach((b) => b.onclick = async () => {
    if (!confirm("Revoke this key? Apps using it stop working.")) return;
    await api(`/api/app/api-keys/${b.dataset.revoke}`, { method: "DELETE" });
    route();
  });
}

// ---------------------------------------------------------------- plans and account
async function pagePlans() {
  const d = await api("/api/app/plans");
  const adminOrgs = (S.me?.orgs || []).filter((o) => o.role === "owner" || o.role === "admin");
  const groups = {};
  d.plans.forEach((p) => (groups[p.stream] = groups[p.stream] || []).push(p));
  view().innerHTML = `
    <section class="hero"><div><span class="tag accent">${d.free ? "Everything is free for now" : "Plans"}</span><h1>Plans</h1>
      <p>${d.free ? `Choose any plan to try it: it starts at once, for 30 days, at ${esc((d.plans[0] || {}).price_display || "0")}. Prices apply later.` : esc(d.tax_note || "")}</p></div></section>
    ${Object.entries(groups).map(([stream, plans]) => `<section><h2>${esc(STREAM_NAMES[stream] || stream)}</h2><div class="plan-grid">${plans.map((p) => `
      <div class="plan"><h3>${esc(p.name)}</h3>
        <div class="price">${esc(p.price_display)}${p.free_now && p.list_display ? `<s>${esc(p.list_display)}${p.per_seat ? " a seat" : ""}</s>` : (p.per_seat ? " a seat" : "")}</div>
        <span class="muted">${esc(p.range)}</span><ul>${p.features.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
        ${p.owner === "org" ? `<select class="field" data-org="${p.id}" aria-label="Workspace">${adminOrgs.filter((o) => !p.org_kind || o.kind === p.org_kind).map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join("") || `<option value="">Create a ${esc(p.org_kind)} workspace first</option>`}</select>` : ""}
        ${p.per_seat ? `<label class="muted">Seats <input type="number" min="1" value="25" class="field" data-seats="${p.id}" /></label>` : ""}
        <button class="btn primary" data-plan="${p.id}">Choose ${esc(p.name)}</button></div>`).join("")}</div></section>`).join("")}`;
  $$("[data-plan]").forEach((b) => b.onclick = async () => {
    if (!(await requireLogin())) return;
    const pid = b.dataset.plan;
    const orgSel = $(`[data-org="${pid}"]`);
    const seats = $(`[data-seats="${pid}"]`);
    try {
      const r = await api("/api/app/checkout", { json: { plan_id: pid, org_id: orgSel ? +orgSel.value || null : null, seats: seats ? +seats.value || 1 : 1 } });
      if (r.status === "active") { toast(`${r.plan} is active for ${r.days} days.`); await refreshMe(); return; }
      if (r.razorpay) payWithRazorpay(r.razorpay);
      if (r.stripe) location.href = r.stripe.url;
    } catch (err) { toast(err.message, true); }
  });
}

function payWithRazorpay(o) {
  const open = () => new window.Razorpay({ ...o, handler: async (res) => {
    try { await api("/api/app/checkout/verify", { json: res }); toast("Payment received. Your plan is active."); await refreshMe(); }
    catch (e) { toast(e.message, true); }
  } }).open();
  if (window.Razorpay) return open();
  const s = document.createElement("script");
  s.src = "https://checkout.razorpay.com/v1/checkout.js";
  s.onload = open;
  document.head.appendChild(s);
}

async function pageAccount() {
  if (!S.me?.user) { view().innerHTML = loginPrompt("Your account", "Log in to see your plans and orders."); return; }
  const o = await api("/api/app/orders");
  const subs = [...(S.me.subscriptions || []).map((s) => ({ ...s, who: "You" })),
    ...(S.me.orgs || []).flatMap((g) => (g.subscriptions || []).map((s) => ({ ...s, who: g.name })))];
  view().innerHTML = `
    <section class="row" style="justify-content:space-between"><div><h1>${esc(S.me.user.name)}</h1><p class="muted">${esc(S.me.user.email)}</p></div>
      <button class="btn" id="logout">Log out</button></section>
    <section class="cols">
      <div class="panel"><h3>Active plans</h3>${subs.length ? `<table><thead><tr><th>Plan</th><th>For</th><th>Until</th></tr></thead><tbody>${subs.map((s) =>
        `<tr><td>${esc(s.plan_id)}</td><td>${esc(s.who)}</td><td>${fmtDate(s.until)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">None. Everything is free for now anyway.</p>`}
        <a class="btn" href="#/plans">See plans</a></div>
      <div class="panel"><h3>Orders</h3>${o.orders.length ? `<table><thead><tr><th>Order</th><th>Plan</th><th>Amount</th><th>Status</th></tr></thead><tbody>${o.orders.map((x) =>
        `<tr><td><code>${esc(x.id)}</code></td><td>${esc(x.plan_id)}</td><td>₹${(x.amount_paise / 100).toLocaleString("en-IN")}</td><td>${esc(x.status)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">No orders yet.</p>`}</div>
      <div class="panel"><h3>Your country</h3><p class="muted">Copyright differs from country to country, so the library shows only titles cleared where you live.</p>
        <select id="country" aria-label="Your country">${Object.entries(S.me.countries || {}).map(([c, n]) =>
          `<option value="${esc(c)}" ${c === S.me.country ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>
    </section>`;
  $("#country").onchange = async (e) => {
    try {
      const r = await api("/api/app/me/country", { json: { country: e.target.value } });
      await refreshMe();
      toast(`Saved. The library now shows titles cleared for ${r.country_name}.`);
    } catch (x) { toast(x.message, true); }
  };
  $("#logout").onclick = async () => { await api("/api/app/logout", { method: "POST" }); await refreshMe(); location.hash = "#/library"; };
}

// ---------------------------------------------------------------- start
window.addEventListener("hashchange", route);
document.addEventListener("DOMContentLoaded", async () => {
  initAuth();
  try { await refreshMe(); } catch (e) { S.me = { user: null }; }
  route();
});
