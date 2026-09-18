// CineCut Studio client 10.0
const state = {
  videoPath: "",
  metadata: null,
  jobId: null,
  scenes: [],
  movieDuration: 0,
  subtitlePath: null,
  health: null,
  socialPack: [],
  guide: null,
  youtubePackText: "",
  rights: null,          // licence decision for the loaded video
  privateItem: null,     // the private-viewing summary on screen: {id, kind, cards}
  privateDeadline: 0,
  termsVersion: null,
  ttlMin: 60
};

const $ = (id) => document.getElementById(id);

// ---------- small utilities ----------
function refreshIcons() {
  try { if (window.lucide && window.lucide.createIcons) window.lucide.createIcons(); } catch (e) { /* icons are optional */ }
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(sec) {
  const s = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`;
}

const safeStorage = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } },
  remove(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }
};

async function api(url, options = {}) {
  const opts = { ...options };
  if (opts.json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const res = await fetch(url, opts);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 428) showTermsModal();
    const detail = data && data.detail !== undefined ? data.detail : data;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail) || `HTTP ${res.status}`);
  }
  return data;
}

function radioValue(name, fallback) {
  const el = document.querySelector(`input[name="${name}"]:checked`);
  return el ? el.value : fallback;
}

function getApiKey() {
  return ($("geminiApiKey").value || "").trim() || safeStorage.get("gemini_api_key") || null;
}

function azureSettings() {
  const key = ($("azureKey").value || "").trim();
  const region = ($("azureRegion").value || "").trim();
  return key && region ? { azure_key: key, azure_region: region } : {};
}

function triggerDownload(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function postDownload(url, filename) {
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const link = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = link;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(link), 5000);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

function cleanTitleGuess(name) {
  return String(name || "")
    .replace(/\.(mp4|mkv|mov|webm|avi|m4v|ts)$/i, "")
    .replace(/\[[^\]]*\]/g, " ")
    .split(/[|｜]/)[0]
    .replace(/\b(full\s+(movie|film)|official\s+(trailer|movie))\b.*$/i, " ")
    .replace(/[_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// ---------- progress modal ----------
function showProgressModal(title, msg, pct = 5) {
  $("modalTitle").textContent = title;
  updateProgressModal(msg, pct);
  $("progressModal").style.display = "flex";
}

function updateProgressModal(msg, pct) {
  $("modalMessage").textContent = msg || "";
  const rounded = Math.round(pct || 0);
  $("progressBarFill").style.width = `${rounded}%`;
  $("progressPctText").textContent = `${rounded}%`;
}

function hideProgressModal() {
  $("progressModal").style.display = "none";
}

function pollTask(taskId, onUpdate, interval = 1000) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const t = await api(`/api/task/${taskId}`);
        if (onUpdate) onUpdate(t);
        if (t.status === "done") { clearInterval(timer); resolve(t); }
        else if (t.status === "error") { clearInterval(timer); reject(new Error(t.error || t.message)); }
      } catch (e) { clearInterval(timer); reject(e); }
    }, interval);
  });
}

function pollJob(jobId, isDone, interval = 1200) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const job = await api(`/api/job/${jobId}`);
        updateProgressModal(job.message, job.progress);
        if (isDone(job)) { clearInterval(timer); resolve(job); }
        else if (job.status === "error") { clearInterval(timer); reject(new Error(job.error || job.message)); }
      } catch (e) { clearInterval(timer); reject(e); }
    }, interval);
  });
}

// ---------- start-up ----------
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initInputs();
  initDurationControls();
  initAiDirector();
  initAdvancedOptions();
  initCopyrightPanel();
  initWatchfolderModal();
  initStreamingModal();
  initStorageModal();
  initTerms();
  initRights();
  initLibrary();
  initExplainer();
  initPrivateBanner();
  checkHealth();
  refreshIcons();
});
window.addEventListener("load", refreshIcons);

function populateVoices(h) {
  const sel = $("voiceSelect");
  const voices = h.voices || {};
  const keep = sel.value;
  sel.innerHTML = "";
  const groups = {};
  Object.entries(voices).forEach(([key, v]) => {
    if (v.ready === false) return;
    (groups[v.language] = groups[v.language] || []).push([key, v.name]);
  });
  Object.entries(groups).forEach(([lang, items]) => {
    const og = document.createElement("optgroup");
    og.label = lang;
    items.forEach(([key, name]) => { const o = document.createElement("option"); o.value = key; o.textContent = name; og.appendChild(o); });
    sel.appendChild(og);
  });
  const lang = $("languageSelect") ? $("languageSelect").value : "English";
  sel.value = (h.default_voices || {})[lang] || keep || "christopher";
}

function renderQuality(q) {
  if (!q) return;
  $("statQuality").textContent = q.score == null ? "–" : `${q.score}/100`;
  const labels = q.labels || {};
  $("qualityPill").title = Object.entries(q.parts || {}).map(([k, v]) => `${labels[k] || k}: ${v}`).join("\n");
  const box = $("qualityIssues");
  box.innerHTML = "";
  if (!q.issues || !q.issues.length) { box.style.display = "none"; return; }
  const ul = document.createElement("ul");
  q.issues.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
  box.appendChild(ul);
  box.style.display = "block";
}

async function runQualityCheck(fix) {
  if (!state.jobId) return;
  showProgressModal(fix ? "Fixing weak spots..." : "Checking the cut...", "An AI is watching the cut like a first-time viewer...", 50);
  try {
    const d = await api(`/api/quality/${state.jobId}`, { json: { ai_check: true, fix } });
    hideProgressModal();
    if (fix && d.fix && d.fix.added) {
      renderDirectorDashboard(await api(`/api/job/${state.jobId}`));
      $("renderStatusText").textContent = `Added ${d.fix.added} scene(s) where a viewer would get lost; quality ${d.before} → ${d.quality.score}. Narrate again before rendering.`;
    } else {
      renderQuality(d.quality);
    }
  } catch (e) {
    hideProgressModal();
    alert(`Quality check error: ${e.message}`);
  }
}

async function checkHealth() {
  try {
    const d = await api("/api/health");
    state.health = d;
    populateVoices(d);
    fillExplainerVoices();
    state.ttlMin = d.private_ttl_min || 60;
    if (d.country) $("libraryCountry").textContent = d.country === "IN" ? "India" : d.country;
    if (d.terms && !d.terms.accepted) showTermsModal(d.terms.version);
    $("gpuText").textContent = d.nvenc ? `${d.gpu_name || "NVIDIA GPU"} · NVENC` : "CPU encoding";
    if (d.nvenc) $("gpuStatus").classList.add("free-badge");
    const port = d.port || 8080;
    $("bookmarkletCode").textContent = `javascript:(function(){var s=document.createElement('script');s.src='http://localhost:${port}/scrubber.js?t='+Date.now();document.body.appendChild(s);})();`;
    if (d.app_dir) $("extensionPath").textContent = `${d.app_dir}\\browser_extension`;
    if (d.local_ai_model) {
      $("aiKeyStatus").textContent = `Without a key, narration and lecture notes are written offline by ${d.local_ai_model} (Ollama). A Gemini key gives better Hindi.`;
    }
    $("narrationEngineHint").textContent = `Each line is written by ${d.ai_writer} from the dialogue of the part that was cut out, never from earlier or later scenes.`;
  } catch (err) {
    $("gpuText").textContent = "Engine offline";
  }
}

// ---------- tabs & inputs ----------
function initTabs() {
  const tabBtns = document.querySelectorAll("#ingestCard .tab-btn");
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabBtns.forEach((b) => b.classList.remove("active"));
      document.querySelectorAll("#ingestCard .tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const target = $(`tab-${btn.dataset.tab}`);
      if (target) target.classList.add("active");
    });
  });
}

function initInputs() {
  $("btnInspectLocal").addEventListener("click", () => {
    const path = $("localFilePath").value.trim();
    if (path) probeVideoPath(path);
  });
  $("localFilePath").addEventListener("keypress", (e) => {
    if (e.key === "Enter" && e.target.value.trim()) probeVideoPath(e.target.value.trim());
  });

  const dropZone = $("dropZone");
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
  });
  $("btnSelectFile").addEventListener("click", () => $("fileUploadInput").click());
  $("fileUploadInput").addEventListener("change", (e) => { if (e.target.files.length) uploadFile(e.target.files[0]); });

  $("btnStartAnalysis").addEventListener("click", startAnalysis);
  $("btnRecalculate").addEventListener("click", recalculateSelection);
  $("btnRenderSummary").addEventListener("click", renderSummaryCut);
  $("btnClosePreview").addEventListener("click", closePreviewModal);
  $("btnOpenFolder").addEventListener("click", openOutputFolder);
  $("btnYoutubePack").addEventListener("click", loadYoutubePack);
  $("btnCopyYoutubePack").addEventListener("click", async () => {
    await copyText(state.youtubePackText);
    $("btnCopyYoutubePack").textContent = "Copied";
    setTimeout(() => { $("btnCopyYoutubePack").textContent = "Copy"; }, 2000);
  });

  const exportMap = { btnExportEdl: "edl", btnExportXml: "xml", btnExportSrt: "srt", btnExportNotes: "notes" };
  Object.entries(exportMap).forEach(([id, kind]) => {
    $(id).addEventListener("click", () => {
      if (state.jobId) triggerDownload(`/api/export/${kind}/${state.jobId}`);
    });
  });
  $("btnGenerateVoBridges").addEventListener("click", triggerVoiceoverGeneration);
  $("btnYoutubeCut").addEventListener("click", () => { if (state.jobId) window.open(`/api/export/youtube_cut/${state.jobId}`, "_blank"); });
  $("btnGenerateShorts").addEventListener("click", triggerShortsGeneration);
  $("btnMakeTrailer").addEventListener("click", makeTrailer);
  $("btnDispatchWebhook").addEventListener("click", dispatchSocialWebhook);
  $("btnRunWhisperASR").addEventListener("click", triggerWhisperTranscription);
  $("btnScanSeason").addEventListener("click", scanSeasonFolder);
  $("btnAssembleSeason").addEventListener("click", assembleSeasonRecap);

  $("btnDownloadDmcaPack").addEventListener("click", () => {
    if (!state.jobId) { alert("Analyze a video first."); return; }
    const ok = confirm("This downloads a DRAFT counter-notification.\n\nA counter-notice is a statement under penalty of perjury. Only send one if you honestly believe the removal was a mistake, and fill in only true statements. A condensed recap of a whole film is unlikely to be fair use.\n\nDownload the draft?");
    if (ok) triggerDownload(`/api/legal/dispute_pack/${state.jobId}`);
  });
  $("btnGenerateLegalSlate").addEventListener("click", async () => {
    if (!state.jobId) { alert("Analyze a video first."); return; }
    try { await postDownload(`/api/legal/slate/${state.jobId}`, "disclosure_slate.mp4"); }
    catch (e) { alert(`Slate error: ${e.message}`); }
  });
}

function initDurationControls() {
  const slider = $("durationSlider");
  const badge = $("targetDurationBadge");
  const pills = document.querySelectorAll(".pill-btn");
  slider.addEventListener("input", (e) => {
    badge.textContent = `${e.target.value} Mins`;
    pills.forEach((p) => p.classList.toggle("active", parseInt(p.dataset.minutes, 10) === parseInt(e.target.value, 10)));
  });
  pills.forEach((btn) => {
    btn.addEventListener("click", () => {
      pills.forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      slider.value = btn.dataset.minutes;
      badge.textContent = `${btn.dataset.minutes} Mins`;
    });
  });
  document.querySelectorAll(".style-card").forEach((card) => {
    const box = card.querySelector('input[type="checkbox"]');
    if (box) {
      box.addEventListener("change", () => card.classList.toggle("active", box.checked));
      return;
    }
    card.addEventListener("click", () => {
      const radio = card.querySelector('input[type="radio"]');
      if (!radio) return;
      document.querySelectorAll(`.style-card input[name="${radio.name}"]`).forEach((r) => r.closest(".style-card").classList.remove("active"));
      card.classList.add("active");
      radio.checked = true;
    });
  });
  const shareSlider = $("filterShare");
  if (shareSlider) shareSlider.addEventListener("input", () => { $("filterShareValue").textContent = `${shareSlider.value}%`; });
}

function initAiDirector() {
  const input = $("geminiApiKey");
  const saved = safeStorage.get("gemini_api_key");
  if (saved) input.value = saved;
  $("btnSaveKey").addEventListener("click", () => {
    const key = input.value.trim();
    if (key) { safeStorage.set("gemini_api_key", key); $("aiKeyStatus").textContent = "Key saved in this browser only."; }
    else { safeStorage.remove("gemini_api_key"); $("aiKeyStatus").textContent = "Key removed."; }
  });
  const toggle = $("toggleAiDirector");
  const section = $("aiConfigSection");
  toggle.checked = safeStorage.get("ai_director_on") === "1";
  section.style.opacity = toggle.checked ? "1" : "0.4";
  toggle.addEventListener("change", () => {
    section.style.opacity = toggle.checked ? "1" : "0.4";
    safeStorage.set("ai_director_on", toggle.checked ? "1" : "0");
  });
}

function initAdvancedOptions() {
  document.querySelectorAll('input[name="renderEngineMode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      document.querySelectorAll(".mode-option").forEach((m) => m.classList.remove("active"));
      radio.closest(".mode-option").classList.add("active");
    });
  });
  document.querySelectorAll('input[name="spoilerDial"], input[name="contentMode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      document.querySelectorAll(`input[name="${radio.name}"]`).forEach((r) => r.closest(".spoiler-card").classList.remove("active"));
      radio.closest(".spoiler-card").classList.add("active");
    });
  });

  const toggleVo = $("toggleVoiceover");
  toggleVo.addEventListener("change", () => { $("voiceSelectorRow").style.opacity = toggleVo.checked ? "1" : "0.5"; });

  const voiceFor = { Spanish: "es_jorge", Hindi: "hi_madhur", French: "fr_henri", German: "de_conrad", Japanese: "ja_keita", English: "christopher" };
  $("languageSelect").addEventListener("change", () => {
    const dv = (state.health && state.health.default_voices) || {};
    const lang = $("languageSelect").value;
    $("voiceSelect").value = dv[lang] || voiceFor[lang] || "christopher";
  });
  $("btnQualityCheck").addEventListener("click", () => runQualityCheck(false));
  $("btnQualityFix").addEventListener("click", () => runQualityCheck(true));

  $("btnTestVoice").addEventListener("click", async () => {
    const btn = $("btnTestVoice");
    btn.disabled = true;
    try {
      const res = await fetch("/api/voice_test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice: $("voiceSelect").value, language: $("languageSelect").value, ...azureSettings() })
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Voice test failed");
      const audio = new Audio(URL.createObjectURL(await res.blob()));
      await audio.play();
    } catch (e) {
      alert(`Voice test failed: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  });
}

// ---------- ingest ----------
async function probeVideoPath(path) {
  showProgressModal("Inspecting video...", "Reading streams and subtitle tracks...");
  try {
    const d = await api("/api/probe", { json: { video_path: path } });
    hideProgressModal();
    handleProbeSuccess(d.metadata);
  } catch (e) {
    hideProgressModal();
    alert(`Error: ${e.message}`);
  }
}

async function downloadUrl(url, basis = null, forcePrivate = false) {
  showProgressModal("Downloading video...", "Starting yt-dlp...", 2);
  try {
    const { task_id } = await api("/api/download", {
      json: { url, max_height: parseInt($("urlMaxHeight").value, 10), rights_basis: basis, force_private: forcePrivate }
    });
    const task = await pollTask(task_id, (t) => updateProgressModal(t.message, t.progress));
    hideProgressModal();
    handleProbeSuccess(task.result.metadata);
  } catch (e) {
    hideProgressModal();
    alert(`Download error: ${e.message}`);
  }
}

async function uploadFile(file) {
  showProgressModal("Uploading video...", `Uploading ${file.name} (${(file.size / 1048576).toFixed(1)} MB)...`);
  const formData = new FormData();
  formData.append("file", file);
  try {
    const d = await api("/api/upload", { method: "POST", body: formData });
    hideProgressModal();
    handleProbeSuccess(d.metadata);
  } catch (e) {
    hideProgressModal();
    alert(`Upload error: ${e.message}`);
  }
}

function handleProbeSuccess(meta) {
  state.metadata = meta;
  state.videoPath = meta.file_path;
  state.movieDuration = meta.duration_sec;
  state.jobId = null;
  if (meta.subtitle_path) {
    state.subtitlePath = meta.subtitle_path;
    $("externalSrtPath").value = meta.subtitle_path;
  } else {
    state.subtitlePath = null;
  }

  $("metaFileName").textContent = meta.youtube_title || meta.file_name;
  $("metaDuration").textContent = meta.duration_formatted;
  $("metaResolution").textContent = `${meta.width}x${meta.height} (${meta.fps} fps)`;
  $("metaEncoder").textContent = meta.nvenc_supported ? "NVENC GPU" : "CPU";

  const whisperBtn = $("btnRunWhisperASR");
  if (meta.subtitle_path) {
    $("metaSubtitles").textContent = "Subtitle file found";
    whisperBtn.style.display = "none";
  } else if (meta.has_subtitles) {
    $("metaSubtitles").textContent = `Embedded: ${meta.subtitle_streams.filter((s) => s.is_text).map((s) => s.language).join(", ")}`;
    whisperBtn.style.display = "none";
  } else {
    $("metaSubtitles").textContent = "None found";
    whisperBtn.style.display = "inline-flex";
    const h = state.health || {};
    whisperBtn.textContent = h.whisper_available ? "Transcribe (Whisper)" : (h.groq_transcription ? "Transcribe (Groq, free)" : "Transcribe");
  }

  $("filmTitleInput").value = cleanTitleGuess(meta.youtube_title || meta.file_name);
  $("metaStrip").style.display = "flex";
  showRightsRow(meta.rights || null);
  $("btnStartAnalysis").disabled = false;
  refreshIcons();
}

async function triggerWhisperTranscription() {
  if (!state.videoPath) return;
  const h = state.health || {};
  let engine = "local";
  if (!h.whisper_available) {
    if (!h.groq_transcription) {
      alert("No transcription engine is available. Install local Whisper (python -m pip install faster-whisper) or add a GROQ_API_KEY to the .env file, then restart CineCut.");
      return;
    }
    if (!confirm("This uploads the video's audio track (not the picture) to Groq and transcribes it with your free-tier key. The free tier allows roughly an hour of audio per hour, so a full film can take a while. Continue?")) return;
    engine = "groq";
  }
  showProgressModal(engine === "groq" ? "Transcribing with Groq Whisper (free tier)..." : "Transcribing locally with Whisper...", "This can take a while for long videos.", 5);
  try {
    const { task_id } = await api("/api/transcribe_video", { json: { video_path: state.videoPath, engine } });
    const task = await pollTask(task_id, (t) => updateProgressModal(t.message, t.progress), 2000);
    hideProgressModal();
    state.subtitlePath = task.result.subtitle_path;
    $("externalSrtPath").value = task.result.subtitle_path;
    $("metaSubtitles").textContent = `Transcribed (${task.result.lines} lines)`;
    $("btnRunWhisperASR").style.display = "none";
  } catch (e) {
    hideProgressModal();
    alert(`Transcription error: ${e.message}`);
  }
}

// ---------- analysis ----------
const PRESET_FOR_FILTER = { song: "musical_romance", romance: "musical_romance", action: "action_energy", comedy: "comedy_fun",
  emotional: "emotional_drama", hero: "hero_spotlight", villain: "villain_lore" };

function pickedFilters() {
  return [...document.querySelectorAll('input[name="filterPick"]:checked')].map((b) => b.value);
}

function currentSettings() {
  const filters = pickedFilters();
  return {
    target_minutes: parseInt($("durationSlider").value, 10),
    preset: filters.length === 0 ? "story_focused" : filters.length === 1 ? PRESET_FOR_FILTER[filters[0]] : "balanced_cinema",
    filters,
    filter_share: parseInt($("filterShare").value, 10) / 100,
    spoiler_mode: radioValue("spoilerDial", "full_cut"),
    target_character: $("characterInput").value.trim() || null,
    enforce_fair_use: $("toggleFairUseShield").checked,
    max_clip_sec: parseFloat($("fairUseMaxClip").value),
    content_mode: radioValue("contentMode", "movie")
  };
}

async function startAnalysis() {
  if (!state.videoPath) return;
  const settings = currentSettings();
  const body = {
    ...settings,
    video_path: state.videoPath,
    use_ai_director: $("toggleAiDirector").checked,
    use_vision: $("toggleVision").checked,
    gemini_api_key: getApiKey(),
    film_title: $("filmTitleInput").value.trim() || null,
    external_srt_path: $("externalSrtPath").value.trim() || state.subtitlePath || null
  };
  const basis = currentBasis();
  if (basis && !$("rightsBasisWrap").hidden && !$("rightsConfirm").checked) {
    alert(DECLARE_CONFIRM);
    return;
  }
  body.rights_basis = basis;
  body.force_private = !basis && !!(state.rights && state.rights.status === "private");
  if (body.use_ai_director && !body.gemini_api_key) {
    alert("AI Director is on but no Gemini API key is saved. Add a key or turn AI Director off.");
    return;
  }
  showProgressModal(settings.content_mode === "lecture" ? "Analyzing lecture..." : "Analyzing story...", "Reading the video...", 5);
  try {
    const { job_id } = await api("/api/analyze", { json: body });
    state.jobId = job_id;
    const job = await pollJob(job_id, (j) => j.status === "ready");
    hideProgressModal();
    renderDirectorDashboard(job);
  } catch (e) {
    hideProgressModal();
    alert(`Analysis error: ${e.message}`);
  }
}

const FILTER_NAMES = { song: "🎶 Song", action: "⚡ Action", comedy: "😂 Comedy", emotional: "💔 Emotional",
  romance: "❤️ Romance", hero: "🦸 Hero", villain: "🦹 Villain" };

const NARRATION_SOURCES = { ai_visual: "from what the AI saw on screen", handwritten: "hand-written story line", ai: "summarized from that dialogue", ai_knowledge: "from the AI's knowledge of the film", database: "from the story beats", time_skip: "time skip", intro: "intro" };

const SOURCE_LABELS = {
  canonical_knowledge_database: "built-in essence database",
  ai_transcript_beats: "film's own dialogue (AI)",
  gemini_cinema_director: "Gemini",
  archetypal_narrative_blueprint: "generic three-act template"
};

function renderWarnings(job) {
  const box = $("analysisWarnings");
  const items = [...(job.warnings || [])];
  if (job.content_mode !== "lecture" && job.timing_note && job.essence_source !== "canonical_knowledge_database") items.push(job.timing_note);
  if (job.content_mode !== "lecture" && job.essence_source === "canonical_knowledge_database" && job.timing_note && !/match/.test(job.timing_note)) items.push(job.timing_note);
  box.innerHTML = "";
  if (!items.length) { box.style.display = "none"; return; }
  const ul = document.createElement("ul");
  items.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
  box.appendChild(ul);
  box.style.display = "block";
}

function renderDirectorDashboard(job) {
  state.scenes = job.scenes || [];
  $("timelineCard").style.display = "flex";
  $("statSelectedTime").textContent = job.total_selected_formatted;
  $("statTargetTime").textContent = `${job.target_minutes}:00`;
  $("statSceneCount").textContent = state.scenes.length;
  $("timelineEndTick").textContent = fmtTime(state.movieDuration);

  const names = [...new Set([...(job.beat_characters || []), ...(job.characters || []).map((c) => c.name)])];
  const list = $("characterList");
  list.innerHTML = "";
  names.forEach((n) => { const o = document.createElement("option"); o.value = n; list.appendChild(o); });
  $("characterInput").value = job.target_character || "";
  $("characterDetectHint").textContent = names.length
    ? `Suggested from the story: ${names.slice(0, 6).join(", ")}${names.length > 6 ? "..." : ""}.`
    : "Type a name as it appears in the dialogue.";
  const used = new Set(job.filters || []);
  document.querySelectorAll('input[name="filterPick"]').forEach((b) => {
    b.checked = used.has(b.value);
    b.closest(".style-card").classList.toggle("active", b.checked);
  });
  if (job.filter_share) {
    $("filterShare").value = Math.round(job.filter_share * 100);
    $("filterShareValue").textContent = `${$("filterShare").value}%`;
  }

  const banner = $("aiOverviewBanner");
  if (job.narrative_overview) {
    $("aiOverviewLabel").textContent = "AI overview:";
    $("aiOverviewText").textContent = job.narrative_overview;
    banner.style.display = "flex";
  } else if (job.essence_theme && job.content_mode !== "lecture") {
    $("aiOverviewLabel").textContent = "Story blueprint:";
    $("aiOverviewText").textContent = `${job.essence_theme} (${(job.essence_milestones || []).length} story beats from the ${SOURCE_LABELS[job.essence_source] || "story engine"})`;
    banner.style.display = "flex";
  } else {
    banner.style.display = "none";
  }

  renderWarnings(job);
  renderQuality(job.quality);
  $("btnYoutubeCut").style.display = job.youtube_id ? "inline-flex" : "none";
  renderTimelineBar(state.scenes);
  renderSceneCards(state.scenes);
  updateFairUseScoreDisplay();
  applyPrivate(job.private ? { id: job.job_id, kind: "job", expiresIn: job.private_expires_in } : null, ["timelineCard", "exportCard"]);
  refreshIcons();
  $("timelineCard").scrollIntoView({ behavior: "smooth" });
}

function actClass(scene) {
  const act = (scene.act || "").toLowerCase();
  if (act.startsWith("topic")) {
    const pos = state.movieDuration ? scene.start / state.movieDuration : 0;
    return pos < 0.33 ? "act-1" : pos < 0.66 ? "act-2" : "act-3";
  }
  if (act.includes("act 1") || act.includes("setup")) return "act-1";
  if (act.includes("act 2") || act.includes("midpoint") || act.includes("cliffhanger")) return "act-2";
  return "act-3";
}

function renderTimelineBar(scenes) {
  const bar = $("timelineBar");
  bar.innerHTML = "";
  const total = state.movieDuration || 1;
  scenes.forEach((scene, idx) => {
    const seg = document.createElement("div");
    seg.className = `timeline-segment ${actClass(scene)} ${scene.selected ? "" : "omitted"}`;
    seg.style.left = `${(scene.start / total) * 100}%`;
    seg.style.width = `${Math.max(0.6, (scene.duration / total) * 100)}%`;
    seg.title = `${scene.act}: ${scene.title} (${scene.start_formatted} - ${scene.end_formatted})`;
    seg.addEventListener("click", () => openPreviewModal(idx));
    bar.appendChild(seg);
  });
}

function renderSceneCards(scenes) {
  const grid = $("sceneCardsGrid");
  grid.innerHTML = "";
  scenes.forEach((scene, idx) => {
    const card = document.createElement("div");
    card.className = `scene-card ${scene.selected ? "" : "omitted"}`;
    card.id = `card-scene-${idx}`;
    const actLabel = (scene.act || "").split(":")[0];
    const narration = scene.bridge_narration || "";
    card.innerHTML = `
      <div class="scene-thumb-wrap" data-action="preview">
        ${scene.thumbnail_url ? `<img src="${escapeHtml(scene.thumbnail_url)}" class="scene-thumb" alt="" loading="lazy" />` : '<div class="scene-thumb" style="background:#1e2337;"></div>'}
        <div class="play-overlay"><div class="play-icon-circle">▶</div></div>
        <span class="scene-duration-badge">${fmtTime(scene.duration)}</span>
      </div>
      <div class="scene-content">
        <div class="scene-meta-row">
          <span class="act-tag ${actClass(scene)}">${escapeHtml(actLabel)}</span>
          ${(scene.filter_match || []).map((t) => `<span class="filter-tag">${escapeHtml(FILTER_NAMES[t] || t)}</span>`).join("")}
          ${scene.tier > 1 ? '<span class="filter-tag muted" title="Added because the cut is long enough for more of the story">Supporting beat</span>' : ""}
          ${narration ? '<span class="bridge-tag">🎙 Narration</span>' : ""}
          ${scene.extended_for_voiceover ? '<span class="bridge-tag" title="Extended so the narration fits">+ fit</span>' : ""}
          <label class="scene-toggle-label"><input type="checkbox" data-action="toggle" ${scene.selected ? "checked" : ""} /> Include</label>
        </div>
        <div class="scene-title">${escapeHtml(scene.title)}</div>
        <div class="scene-time">${escapeHtml(scene.start_formatted)} → ${escapeHtml(scene.end_formatted)}</div>
        ${narration ? `<div class="preview-bridge-banner" style="margin-top:0.3rem;font-size:0.75rem;">"${escapeHtml(narration)}"</div>` : ""}
        ${scene.narration_covers ? `<div class="scene-time">Narration covers the cut ${escapeHtml(scene.narration_covers.start_formatted)} to ${escapeHtml(scene.narration_covers.end_formatted)} · ${escapeHtml(NARRATION_SOURCES[scene.narration_source] || "")}</div>` : ""}
        ${scene.voiceover_url ? `<audio controls preload="none" src="${escapeHtml(scene.voiceover_url)}" class="scene-audio"></audio>` : ""}
        <div class="scene-reason">${escapeHtml(scene.reason || "")}</div>
        ${scene.dialogue ? `<div class="scene-dialogue">"${escapeHtml(scene.dialogue.slice(0, 280))}"</div>` : ""}
      </div>`;
    card.querySelector('[data-action="preview"]').addEventListener("click", () => openPreviewModal(idx));
    card.querySelector('[data-action="toggle"]').addEventListener("change", (e) => toggleSceneSelection(idx, e.target.checked));
    grid.appendChild(card);
  });
}

async function toggleSceneSelection(idx, isSelected) {
  if (!state.jobId) return;
  state.scenes[idx].selected = isSelected;
  const card = $(`card-scene-${idx}`);
  if (card) card.classList.toggle("omitted", !isSelected);
  try {
    const d = await api(`/api/toggle_scene/${state.jobId}`, { json: { scene_index: idx, selected: isSelected } });
    $("statSelectedTime").textContent = d.total_selected_formatted;
    api(`/api/job/${state.jobId}`).then((j) => renderQuality(j.quality)).catch(() => {});
    renderTimelineBar(state.scenes);
    updateFairUseScoreDisplay();
  } catch (e) {
    console.error(e);
  }
}

async function recalculateSelection() {
  if (!state.jobId) return;
  const settings = currentSettings();
  showProgressModal("Recalculating...", "Re-budgeting the story within the new settings...", 50);
  try {
    const d = await api(`/api/recalculate/${state.jobId}`, { json: settings });
    hideProgressModal();
    const job = await api(`/api/job/${state.jobId}`);
    renderDirectorDashboard(job);
    $("statSelectedTime").textContent = d.total_selected_formatted;
  } catch (e) {
    hideProgressModal();
    alert(`Recalculate error: ${e.message}`);
  }
}

// ---------- narration ----------
async function triggerVoiceoverGeneration() {
  if (!state.jobId) return;
  const language = $("languageSelect").value;
  showProgressModal("Generating narration...", `Writing and voicing narration in ${language}...`, 40);
  try {
    const d = await api(`/api/narrate/${state.jobId}`, {
      json: { voice: $("voiceSelect").value, language, style: $("narrationStyle").value, gemini_api_key: getApiKey(), ...azureSettings() }
    });
    hideProgressModal();
    state.scenes = d.scenes;
    $("toggleVoiceover").checked = true;
    $("voiceSelectorRow").style.opacity = "1";
    renderSceneCards(state.scenes);
    updateFairUseScoreDisplay();
    const voiced = state.scenes.filter((s) => s.voiceover_url).length;
    const st = d.stats || {};
    const parts = st.style !== "scene_intro" ? ` ${st.handwritten || 0} hand-written story lines. Written by ${st.provider}: ${st.ai || 0} from the cut-out dialogue, ${st.ai_knowledge || 0} from the AI's knowledge of the film, ${st.database || 0} from story beats, ${st.time_skip || 0} time skips.` : "";
    $("renderStatusText").textContent = `Narration ready for ${voiced} scenes.${parts} Listen on the scene cards, then render.`;
  } catch (e) {
    hideProgressModal();
    alert(`Narration error: ${e.message}`);
  }
}

// ---------- trailer ----------
async function makeTrailer() {
  if (!state.jobId) return;
  const vertical = $("trailerVertical").checked;
  showProgressModal("Making the trailer...", "Picking lines and moments, then rendering with a title card...", 40);
  try {
    const d = await api(`/api/trailer/${state.jobId}`, {
      json: { length_sec: parseInt($("trailerLength").value, 10), spoiler_free: true, vertical }
    });
    hideProgressModal();
    $("trailerSection").style.display = "block";
    $("trailerPlayer").style.maxWidth = vertical ? "320px" : "100%";
    $("trailerPlayer").src = `${d.video_url}?t=${Date.now()}`;
    $("btnDownloadTrailer").href = `${d.video_url}?download=true`;
    const p = d.parts || {};
    $("trailerPlan").textContent = `${d.duration_formatted}: ${p.setup || 0} setup lines, ${p.build || 0} build-up moments, ` +
      `${p.montage || 0} montage shots, a closing line and a title card. Uses only the first ${d.uses_film_up_to_pct}% of the film.`;
    $("trailerSection").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    hideProgressModal();
    alert(`Trailer error: ${e.message}`);
  }
}

// ---------- shorts ----------
async function triggerShortsGeneration() {
  if (!state.jobId) return;
  const pan = $("togglePanAndScan").checked;
  showProgressModal("Generating shorts...", pan ? "Finding the loudest moments and tracking faces for the 9:16 crop..." : "Finding the loudest moments and framing them in 9:16...", 30);
  try {
    const d = await api(`/api/generate_shorts/${state.jobId}`, { json: { pan_and_scan: pan, burn_captions: $("toggleBurnCaptions").checked, caption_style: $("captionStyle").value } });
    hideProgressModal();
    renderShortsGallery(d.viral_shorts);
    loadSocialPublishingPack();
  } catch (e) {
    hideProgressModal();
    alert(`Shorts error: ${e.message}`);
  }
}

function renderShortsGallery(shorts) {
  const grid = $("shortsGrid");
  grid.innerHTML = "";
  shorts.forEach((s) => {
    const card = document.createElement("div");
    card.className = "short-card";
    card.innerHTML = `
      <video src="${escapeHtml(s.stream_url)}?t=${Date.now()}" controls class="short-video-preview" preload="metadata"></video>
      <div class="short-card-info">
        <span class="short-title">${escapeHtml(s.title)}</span>
        <span class="short-dur">${Math.round(s.duration)}s · 9:16 · hook score ${Math.round(s.hook_score || 0)}${s.captions ? " · captions" : ""}</span>
        <a href="${escapeHtml(s.download_url)}" class="btn btn-sm btn-secondary private-hide" style="margin-top:0.3rem;">⬇ Download</a>
      </div>`;
    grid.appendChild(card);
  });
  $("btnDownloadShortsZip").href = `/api/export/shorts_pack/${state.jobId}`;
  $("viralShortsSection").style.display = "flex";
  $("viralShortsSection").scrollIntoView({ behavior: "smooth" });
  refreshIcons();
}

async function loadSocialPublishingPack() {
  try {
    const d = await api(`/api/social/pack/${state.jobId}`);
    state.socialPack = d.pack || [];
    const list = $("socialCardsList");
    list.innerHTML = "";
    state.socialPack.forEach((item, i) => {
      const card = document.createElement("div");
      card.className = "social-card-item";
      card.innerHTML = `
        <div class="social-card-top">
          <span class="social-card-title">Short #${item.short_index}: ${escapeHtml(item.viral_title)}</span>
          <button class="social-copy-btn" data-i="${i}">Copy</button>
        </div>
        <div class="social-card-desc">${escapeHtml(item.description).replace(/\n/g, "<br>")}</div>`;
      card.querySelector("button").addEventListener("click", async (e) => {
        await copyText(state.socialPack[i].copy_paste_text);
        e.target.textContent = "Copied";
        setTimeout(() => { e.target.textContent = "Copy"; }, 2000);
      });
      list.appendChild(card);
    });
    $("socialHubCard").style.display = "block";
  } catch (e) {
    console.error("Posting pack failed", e);
  }
}

async function dispatchSocialWebhook() {
  if (!state.jobId) return;
  const url = $("socialWebhookUrl").value.trim();
  if (!url) { alert("Enter your webhook URL first."); return; }
  try {
    await api(`/api/social/webhook/${state.jobId}`, { json: { webhook_url: url } });
    alert("Sent to your webhook.");
  } catch (e) {
    alert(`Webhook error: ${e.message}`);
  }
}

// ---------- preview ----------
function openPreviewModal(idx) {
  const scene = state.scenes[idx];
  if (!state.jobId || !scene) return;
  $("previewTitle").textContent = `${scene.act}: ${scene.title}`;
  $("previewTimestamp").textContent = `${scene.start_formatted} → ${scene.end_formatted} (${fmtTime(scene.duration)})${scene.duration > 20 ? " · preview shows the first 20 s" : ""}`;
  $("previewDialogue").textContent = scene.dialogue ? `"${scene.dialogue.slice(0, 400)}"` : (scene.reason || "");
  if (scene.bridge_narration) {
    $("previewBridgeText").textContent = scene.bridge_narration;
    $("previewBridgeBox").style.display = "flex";
  } else {
    $("previewBridgeBox").style.display = "none";
  }
  $("previewPlayer").src = `/api/preview/${state.jobId}/${idx}`;
  $("previewModal").style.display = "flex";
}

function closePreviewModal() {
  const player = $("previewPlayer");
  player.pause();
  player.removeAttribute("src");
  player.load();
  $("previewModal").style.display = "none";
}

// ---------- render ----------
async function renderSummaryCut() {
  if (!state.jobId) return;
  const body = {
    render_mode: radioValue("renderEngineMode", "cinematic_nvenc"),
    include_voiceover: $("toggleVoiceover").checked,
    normalize_audio: $("toggleAudioNorm").checked,
    smooth_audio: $("toggleAudioSmooth").checked,
    horizontal_flip: $("toggleHorizontalFlip").checked,
    voice: $("voiceSelect").value,
    language: $("languageSelect").value,
    ...azureSettings()
  };
  showProgressModal("Rendering summary video...", "Preparing...", 2);
  try {
    await api(`/api/render/${state.jobId}`, { json: body });
    const job = await pollJob(state.jobId, (j) => j.status === "rendered", 1000);
    hideProgressModal();
    handleRenderSuccess(job);
  } catch (e) {
    hideProgressModal();
    alert(`Render error: ${e.message}`);
  }
}

function handleRenderSuccess(job) {
  state.scenes = job.scenes;
  renderSceneCards(state.scenes);
  $("statSelectedTime").textContent = job.total_selected_formatted;
  $("finalVideoPlayer").src = `/api/video/final/${state.jobId}?t=${Date.now()}`;
  $("btnDownloadVideo").href = `/api/video/final/${state.jobId}?download=true`;
  if (job.private) $("finalVideoPlayer").setAttribute("controlsList", "nodownload");
  else $("finalVideoPlayer").removeAttribute("controlsList");
  $("renderedInfo").textContent = job.message || "Chapters embedded";
  $("youtubePackBox").style.display = "none";
  $("exportCard").style.display = "flex";
  $("exportCard").scrollIntoView({ behavior: "smooth" });
  refreshIcons();
}

async function openOutputFolder() {
  try { await api(`/api/open_folder/${state.jobId || "none"}`, { method: "POST" }); } catch (e) { console.error(e); }
}

async function loadYoutubePack() {
  if (!state.jobId) return;
  try {
    const p = await api(`/api/youtube/pack/${state.jobId}`);
    state.youtubePackText = `TITLE:\n${p.title}\n\nDESCRIPTION:\n${p.description}\n\nTAGS:\n${p.tags.join(", ")}\n\nCATEGORY: ${p.category}`;
    $("youtubePackText").value = state.youtubePackText;
    const ul = $("youtubeChecklist");
    ul.innerHTML = "";
    p.checklist.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
    $("youtubePackBox").style.display = "block";
  } catch (e) {
    alert(`YouTube pack error: ${e.message}`);
  }
}

// ---------- copyright check ----------
function initCopyrightPanel() {
  const toggle = $("toggleFairUseShield");
  const controls = $("fairUseControls");
  toggle.addEventListener("change", () => {
    controls.style.opacity = toggle.checked ? "1" : "0.5";
    controls.style.pointerEvents = toggle.checked ? "auto" : "none";
    updateFairUseScoreDisplay();
  });
  $("toggleHorizontalFlip").addEventListener("change", updateFairUseScoreDisplay);
  $("fairUseMaxClip").addEventListener("change", () => {
    updateFairUseScoreDisplay();
    if (state.jobId) $("defenseScoreHint").textContent = "Click Recalculate to split clips at the new length.";
  });
}

async function updateFairUseScoreDisplay() {
  const on = $("toggleFairUseShield").checked;
  const val = $("defenseScoreVal"), fill = $("defenseMeterFill"), hint = $("defenseScoreHint");
  if (!on) {
    val.textContent = "Inactive";
    val.style.color = "var(--text-muted)";
    fill.style.width = "10%";
    hint.textContent = "Turn on to split long clips and see the checklist.";
    return;
  }
  if (!state.jobId) {
    val.textContent = "-";
    hint.textContent = "Analyze a video to see the checklist.";
    return;
  }
  try {
    const d = await api(`/api/fair_use/score/${state.jobId}?horizontal_flip=${$("toggleHorizontalFlip").checked}`);
    val.textContent = `${d.total_score}/100 · ${d.rating}`;
    val.style.color = d.total_score >= 75 ? "#10b981" : d.total_score >= 50 ? "#f59e0b" : "#f43f5e";
    fill.style.width = `${Math.max(5, d.total_score)}%`;
    const share = d.source_share_pct !== null && d.source_share_pct !== undefined ? `${d.source_share_pct}% of the film used · ` : "";
    hint.textContent = `${share}longest clip ${d.longest_clip_sec}s · commentary on ${d.vo_coverage_pct}% of scenes. ${d.recommendations[0] || ""}`;
    hint.title = d.recommendations.join("\n\n");
  } catch (e) {
    hint.textContent = `Could not compute the checklist: ${e.message}`;
  }
}

// ---------- TV season batch ----------
let currentSeasonData = null;

async function scanSeasonFolder() {
  const folderPath = $("seasonFolderPath").value.trim();
  if (!folderPath) { alert("Enter the season folder path."); return; }
  showProgressModal("Scanning season folder...", "Reading episodes...", 20);
  try {
    currentSeasonData = await api("/api/season/scan", { json: { folder_path: folderPath } });
    hideProgressModal();
    $("seasonEpisodesSummaryText").textContent = `Season ${currentSeasonData.season_num}: ${currentSeasonData.total_episodes} episodes (${currentSeasonData.total_runtime_formatted})`;
    const list = $("episodesList");
    list.innerHTML = "";
    currentSeasonData.episodes.forEach((ep) => {
      const item = document.createElement("div");
      item.className = "episode-item";
      item.innerHTML = `<div><span class="ep-badge">E${String(ep.episode).padStart(2, "0")}</span> <strong>${escapeHtml(ep.title)}</strong></div><div><span class="ep-dur">${escapeHtml(ep.duration_formatted)}</span></div>`;
      list.appendChild(item);
    });
    $("seasonEpisodesContainer").style.display = "block";
  } catch (e) {
    hideProgressModal();
    alert(`Season scan error: ${e.message}`);
  }
}

async function assembleSeasonRecap() {
  if (!currentSeasonData) return;
  const target = parseFloat($("seasonTargetDuration").value);
  showProgressModal("Assembling season recap...", `Curating ${currentSeasonData.total_episodes} episodes into ${target} minutes...`, 5);
  try {
    const { batch_id } = await api("/api/season/process", {
      json: { folder_path: currentSeasonData.folder_path, target_minutes: target, preset: "story_focused", render_mode: radioValue("renderEngineMode", "cinematic_nvenc") }
    });
    const timer = setInterval(async () => {
      try {
        const d = await api(`/api/season/status/${batch_id}`);
        updateProgressModal(d.message, d.progress);
        if (d.status === "completed") {
          clearInterval(timer);
          hideProgressModal();
          const warn = d.warnings && d.warnings.length ? `\n\n${d.warnings.length} clip(s) were skipped.` : "";
          alert(`Season recap ready: ${d.filename} (${d.total_duration_formatted}).${warn}`);
          triggerDownload(`/api/season/download/${batch_id}`);
        } else if (d.status === "error") {
          clearInterval(timer);
          hideProgressModal();
          alert(`Season error: ${d.error || d.message}`);
        }
      } catch (e) {
        clearInterval(timer);
        hideProgressModal();
      }
    }, 1500);
  } catch (e) {
    hideProgressModal();
    alert(`Season error: ${e.message}`);
  }
}

// ---------- watchfolder ----------
let watchfolderPoll = null;

function initWatchfolderModal() {
  $("btnOpenWatchfolderModal").addEventListener("click", () => {
    $("watchfolderModal").style.display = "flex";
    fetchWatchfolderStatus();
    if (!watchfolderPoll) watchfolderPoll = setInterval(fetchWatchfolderStatus, 3000);
  });
  $("btnCloseWatchfolder").addEventListener("click", () => {
    $("watchfolderModal").style.display = "none";
    clearInterval(watchfolderPoll);
    watchfolderPoll = null;
  });
  $("btnToggleDaemon").addEventListener("click", toggleWatchfolderDaemon);
  $("btnTriggerScanNow").addEventListener("click", async () => {
    try {
      const d = await api("/api/watchfolder/scan", { method: "POST" });
      renderWatchfolderStatus(d.status);
    } catch (e) {
      alert(`Scan error: ${e.message}`);
    }
  });
}

async function fetchWatchfolderStatus() {
  try { renderWatchfolderStatus(await api("/api/watchfolder/status")); } catch (e) { /* ignore */ }
}

function renderWatchfolderStatus(d) {
  if (d.watch_dir && !$("watchfolderInput").value) $("watchfolderInput").value = d.watch_dir;
  const pill = $("daemonStatusPill");
  if (d.running) {
    $("daemonStatusText").textContent = `Watching (${d.completed_count} recaps made)`;
    pill.style.borderColor = "#10b981";
    pill.style.color = "#10b981";
    $("btnToggleDaemon").textContent = "■ Stop";
  } else {
    $("daemonStatusText").textContent = "Inactive";
    pill.style.borderColor = "";
    pill.style.color = "";
    $("btnToggleDaemon").textContent = "▶ Start";
  }
  const logs = $("daemonLogsList");
  if (d.recent_logs && d.recent_logs.length) {
    logs.innerHTML = "";
    d.recent_logs.forEach((l) => { const div = document.createElement("div"); div.textContent = `[${l.timestamp}] ${l.message}`; logs.appendChild(div); });
    logs.scrollTop = logs.scrollHeight;
  }
}

async function toggleWatchfolderDaemon() {
  try {
    const status = await api("/api/watchfolder/status");
    if (status.running) {
      await api("/api/watchfolder/stop", { method: "POST" });
    } else {
      const folder = $("watchfolderInput").value.trim();
      if (!folder) { alert("Enter a folder to watch."); return; }
      await api("/api/watchfolder/start", {
        json: { watch_dir: folder, target_minutes: parseInt($("watchfolderDuration").value, 10), render_mode: $("watchfolderEngine").value, check_interval_sec: 5 }
      });
    }
    fetchWatchfolderStatus();
  } catch (e) {
    alert(`Watchfolder error: ${e.message}`);
  }
}

// ---------- Netflix / Prime watch guide & TV ----------
function initStreamingModal() {
  const modal = $("tvStreamingModal");
  $("btnOpenTvStreamingModal").addEventListener("click", () => { modal.style.display = "flex"; fetchTvNetworkInfo(); refreshIcons(); });
  $("btnCloseTvStreaming").addEventListener("click", () => { modal.style.display = "none"; });

  const tabs = [["tabBtnStreaming", "subtabStreaming"], ["tabBtnSmartTv", "subtabSmartTv"]];
  tabs.forEach(([btnId, paneId]) => {
    $(btnId).addEventListener("click", () => {
      tabs.forEach(([b, p]) => { $(b).classList.toggle("active", b === btnId); $(p).style.display = p === paneId ? "block" : "none"; });
    });
  });

  $("btnCopyBookmarklet").addEventListener("click", () => copyText($("bookmarkletCode").textContent.trim()));
  $("btnCopyTvUrl").addEventListener("click", () => copyText($("tvModalLanUrl").textContent.trim()));
  $("btnBuildGuide").addEventListener("click", buildWatchGuide);
  $("btnGuideAudio").addEventListener("click", buildGuideAudio);
  $("btnCopyGuide").addEventListener("click", async () => {
    if (!state.guide) return;
    await copyText(state.guide.text);
    $("btnCopyGuide").textContent = "Copied";
    setTimeout(() => { $("btnCopyGuide").textContent = "Copy guide"; }, 2000);
  });
}

async function fetchTvNetworkInfo() {
  try {
    const d = await api("/api/tv/network_info");
    const notice = $("tvLanNotice");
    if (d.lan_mode) {
      $("tvModalLanUrl").textContent = d.tv_url;
      notice.style.display = "none";
    } else {
      $("tvModalLanUrl").textContent = `http://localhost:${d.port}/tv (this PC only)`;
      notice.textContent = "Network sharing is off, so a TV cannot connect yet. Restart CineCut with: python run.py --lan. Only the TV player is shared; the editor stays private to this PC.";
      notice.style.display = "block";
    }
  } catch (e) { /* ignore */ }
}

function guideRequestBody() {
  return {
    title: $("wgTitle").value.trim(),
    runtime_min: parseFloat($("wgRuntime").value),
    target_minutes: parseFloat($("wgTarget").value),
    language: $("wgLanguage").value,
    spoiler_mode: $("wgSpoiler").value,
    offset_sec: parseFloat($("wgOffset").value || "0"),
    gemini_api_key: getApiKey(),
    voice: $("wgLanguage").value === "Hindi" ? "hi_madhur" : "christopher"
  };
}

async function buildWatchGuide() {
  const body = guideRequestBody();
  if (!body.title || !(body.runtime_min > 10)) { alert("Enter the film title and its runtime in minutes."); return; }
  $("btnBuildGuide").disabled = true;
  try {
    const g = await api("/api/watchguide", { json: body });
    state.guide = g;
    $("wgAccuracy").textContent = `${g.title}: watch about ${g.total_watch_formatted} of ${g.runtime_formatted}. ${g.accuracy_note}`;
    $("wgAccuracy").classList.toggle("warn", !!g.is_generic);
    const list = $("wgList");
    list.innerHTML = "";
    g.items.forEach((it) => {
      const li = document.createElement("li");
      const head = document.createElement("div");
      head.innerHTML = `<strong>${escapeHtml(it.jump_to)} → ${escapeHtml(it.watch_until)}</strong> ${escapeHtml(it.title)}${it.skip_before_sec > 20 ? ` <span class="skip">skip ${fmtTime(it.skip_before_sec)}</span>` : ""}`;
      li.appendChild(head);
      if (it.recap) {
        const p = document.createElement("div");
        p.className = "recap";
        p.textContent = it.recap;
        li.appendChild(p);
      }
      list.appendChild(li);
    });
    $("wgResult").style.display = "block";
    $("btnGuideAudio").disabled = false;
    $("btnCopyGuide").disabled = false;
  } catch (e) {
    alert(`Watch guide error: ${e.message}`);
  } finally {
    $("btnBuildGuide").disabled = false;
  }
}

async function buildGuideAudio() {
  if (!state.guide) return;
  $("btnGuideAudio").disabled = true;
  try {
    const d = await api("/api/watchguide/audio", { json: guideRequestBody() });
    const player = $("wgAudioPlayer");
    player.src = d.audio_url;
    player.style.display = "block";
    player.play().catch(() => {});
  } catch (e) {
    alert(`Audio recap error: ${e.message}`);
  } finally {
    $("btnGuideAudio").disabled = false;
  }
}

// ---------- storage ----------
function initStorageModal() {
  $("btnOpenStorageModal").addEventListener("click", () => { $("storageModal").style.display = "flex"; loadStorage(); });
  $("btnCloseStorage").addEventListener("click", () => { $("storageModal").style.display = "none"; });
  $("btnRunCleanup").addEventListener("click", runCleanup);
}

async function loadStorage() {
  const list = $("storageUsageList");
  try {
    const d = await api("/api/maintenance/storage");
    const labels = { downloads: "Downloaded videos", uploads: "Uploaded videos", job_workspaces: `Temporary job files (${d.workspace_count} folders)`, output_videos: "Rendered videos (output)" };
    list.innerHTML = "";
    Object.entries(labels).forEach(([k, label]) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(d.formatted[k])}</strong>`;
      list.appendChild(li);
    });
  } catch (e) {
    list.textContent = `Could not read storage: ${e.message}`;
  }
}

async function runCleanup() {
  const days = parseFloat($("cleanupDays").value || "7");
  const extra = [$("cleanupDownloads").checked ? "downloaded videos" : "", $("cleanupUploads").checked ? "uploaded videos" : ""].filter(Boolean).join(" and ");
  if (!confirm(`Permanently delete temporary files older than ${days} day(s)${extra ? ` including ${extra}` : ""}? Rendered videos are kept.`)) return;
  try {
    const d = await api("/api/maintenance/cleanup", { json: { older_than_days: days, include_downloads: $("cleanupDownloads").checked, include_uploads: $("cleanupUploads").checked } });
    $("cleanupResult").textContent = `Removed ${d.removed_items} item(s), freed ${d.freed_formatted}.`;
    loadStorage();
  } catch (e) {
    $("cleanupResult").textContent = `Cleanup failed: ${e.message}`;
  }
}

// ---------- terms ----------
function initTerms() {
  $("termsAgree").addEventListener("change", (e) => { $("btnAcceptTerms").disabled = !e.target.checked; });
  $("btnAcceptTerms").addEventListener("click", async () => {
    try {
      await api("/api/terms/accept", { json: { version: state.termsVersion, agree: true } });
      $("termsModal").style.display = "none";
    } catch (e) {
      alert(`Could not save your acceptance: ${e.message}`);
    }
  });
}

async function showTermsModal(version) {
  if (version) state.termsVersion = version;
  if (!state.termsVersion) {
    try {
      const s = await api("/api/terms/status");
      state.termsVersion = s.version;
      state.ttlMin = s.private_ttl_min || state.ttlMin;
    } catch (e) { /* the server is offline; the modal still explains the rules */ }
  }
  $("termsVersion").textContent = state.termsVersion || "";
  $("termsTtl").textContent = state.ttlMin || 60;
  $("termsModal").style.display = "flex";
}

// ---------- rights check ----------
const RIGHTS_LABELS = { cleared: "Cleared", declared: "Declared by you", private: "Private viewing", blocked: "Blocked" };
const DECLARE_CONFIRM = "Tick the confirmation to declare your rights, or choose private viewing.";

function rightsChip(status) {
  return `<span class="rights-chip ${escapeHtml(status)}">${escapeHtml(RIGHTS_LABELS[status] || status)}</span>`;
}

function initRights() {
  $("btnCheckRights").addEventListener("click", () => { const url = $("videoUrl").value.trim(); if (url) checkLinkRights(url); });
  $("videoUrl").addEventListener("keypress", (e) => { if (e.key === "Enter" && e.target.value.trim()) checkLinkRights(e.target.value.trim()); });
  $("rightsBasis").addEventListener("change", () => { $("rightsConfirmWrap").hidden = $("rightsBasis").value === "private"; });
}

async function checkLinkRights(url) {
  const panel = $("rightsPanel");
  panel.hidden = false;
  panel.innerHTML = `<p class="field-hint">Reading the licence at the source...</p>`;
  try {
    renderRightsPanel(await api("/api/rights/check", { json: { url } }), url);
  } catch (e) {
    panel.innerHTML = `<p class="rights-error">${escapeHtml(e.message)}</p>`;
  }
}

function renderRightsPanel(r, url) {
  const panel = $("rightsPanel");
  const lic = r.license || {};
  const notes = [...(r.reasons || []), ...(r.caveats || [])].map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  let actions;
  if (r.status === "blocked") {
    actions = `<button class="btn btn-secondary" id="rpGuide"><i data-lucide="list-video"></i> Use the Watch Guide instead</button>`;
  } else if (r.status === "cleared") {
    actions = `<button class="btn btn-primary" id="rpDownload"><i data-lucide="download"></i> <span>Download</span></button>
      <label class="checkbox-row"><input type="checkbox" id="rpForcePrivate" /><span>Keep it private anyway (deleted after viewing)</span></label>`;
  } else {
    actions = `<label class="rights-basis">Your rights:
        <select id="rpBasis" class="styled-select-sm">
          <option value="private" selected>None of these: private viewing, deleted afterwards</option>
          <option value="own">I made it or own all its rights</option>
          <option value="permission">I have written permission from the rights owner</option>
        </select></label>
      <label class="checkbox-row" id="rpConfirmWrap" hidden><input type="checkbox" id="rpConfirm" /><span>I confirm this is true and accept responsibility for it under the <a href="/terms" target="_blank" rel="noopener">Terms</a> (section 13).</span></label>
      <button class="btn btn-primary" id="rpDownload"><i data-lucide="download"></i> <span>Download for private viewing</span></button>`;
  }
  const facts = [lic.label, r.creator, r.year].filter(Boolean).map((x) => escapeHtml(x)).join(" · ");
  panel.innerHTML = `
    <div class="rights-head">${rightsChip(r.status)}<strong>${escapeHtml(r.title || url)}</strong><span class="rights-lic">${facts}</span></div>
    <ul class="rights-reasons">${notes}</ul>
    <div class="rights-meta">Checked with: ${escapeHtml(r.source || "")}. Automatic checks can be wrong, and you stay responsible for what you use.</div>
    <div class="rights-actions">${actions}</div>`;
  if ($("rpGuide")) $("rpGuide").addEventListener("click", () => $("btnOpenTvStreamingModal").click());
  const basis = $("rpBasis");
  if (basis) basis.addEventListener("change", () => {
    const declared = basis.value !== "private";
    $("rpConfirmWrap").hidden = !declared;
    $("rpDownload").querySelector("span").textContent = declared ? "Download" : "Download for private viewing";
  });
  if ($("rpDownload")) $("rpDownload").addEventListener("click", () => {
    const b = basis && basis.value !== "private" ? basis.value : null;
    if (b && !$("rpConfirm").checked) { alert(DECLARE_CONFIRM); return; }
    downloadUrl(url, b, $("rpForcePrivate") ? $("rpForcePrivate").checked : false);
  });
  refreshIcons();
}

function showRightsRow(r) {
  state.rights = r;
  const status = r ? r.status : "private";
  const chip = $("rightsChip");
  chip.className = `rights-chip ${status}`;
  chip.textContent = RIGHTS_LABELS[status] || status;
  const lic = r && r.license ? r.license.label : "";
  $("rightsText").textContent = !r ? "A file on this PC carries no licence information. Choose what applies:"
    : status === "cleared" ? `${lic}${(r.caveats || [])[0] ? `. ${r.caveats[0]}` : ""}`
    : status === "declared" ? "You declared your rights when downloading; this is recorded."
    : "No verified licence: the summary is shown here and then deleted.";
  $("rightsBasisWrap").hidden = !(!r || status === "private");
  $("rightsBasis").value = "private";
  $("rightsConfirmWrap").hidden = true;
  $("rightsConfirm").checked = false;
  $("rightsRow").hidden = false;
}

function currentBasis() {
  if (!$("rightsBasisWrap").hidden) {
    const v = $("rightsBasis").value;
    return v === "private" ? null : v;
  }
  return state.rights && ["own", "permission"].includes(state.rights.basis) ? state.rights.basis : null;
}

// ---------- private viewing ----------
function initPrivateBanner() {
  $("btnDeletePrivate").addEventListener("click", deletePrivateNow);
  setInterval(showPrivateBanner, 15000);
}

function applyPrivate(item, cardIds) {
  cardIds.forEach((id) => { const el = $(id); if (el) el.dataset.private = item ? "1" : "0"; });
  if (item) {
    state.privateItem = { ...item, cards: cardIds };
    const secs = item.expiresIn != null ? item.expiresIn : (state.ttlMin || 60) * 60;
    state.privateDeadline = Date.now() + secs * 1000;
  } else if (state.privateItem && state.privateItem.cards.some((c) => cardIds.includes(c))) {
    state.privateItem = null;
  }
  showPrivateBanner();
}

function showPrivateBanner() {
  const it = state.privateItem;
  if (!it) { $("privateBanner").hidden = true; return; }
  const mins = Math.max(1, Math.ceil((state.privateDeadline - Date.now()) / 60000));
  const what = it.kind === "explainer" ? "explainer" : "summary";
  $("privateBannerText").textContent = `Private viewing: this ${what} has no verified licence, so downloads are off. ` +
    `It is deleted in about ${mins} min, or now if you press Delete now.`;
  $("privateBanner").hidden = false;
}

async function deletePrivateNow() {
  const it = state.privateItem;
  if (!it) return;
  if (!confirm("Delete this private summary now? What CineCut downloaded or made for it (source, transcript, narration, video) is removed for good.")) return;
  // Windows keeps a file locked while it streams, so stop every player first
  document.querySelectorAll("video").forEach((v) => { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) { /* ignore */ } });
  await new Promise((r) => setTimeout(r, 400));
  try {
    const d = await api(`/api/private/${it.id}/delete`, { method: "POST" });
    it.cards.forEach((id) => { const el = $(id); if (el) { el.dataset.private = "0"; el.style.display = "none"; } });
    if (it.kind === "job") {
      state.jobId = null;
      $("metaStrip").style.display = "none";
      $("rightsRow").hidden = true;
      $("btnStartAnalysis").disabled = true;
    }
    state.privateItem = null;
    showPrivateBanner();
    alert(`Deleted ${d.removed_items} item(s), ${Math.round((d.freed_bytes || 0) / 1048576)} MB.`);
  } catch (e) {
    alert(`Delete failed: ${e.message}`);
  }
}

// ---------- free library ----------
function initLibrary() {
  $("btnLibrarySearch").addEventListener("click", searchLibrary);
  $("libraryQuery").addEventListener("keypress", (e) => { if (e.key === "Enter") searchLibrary(); });
}

function switchTab(name) {
  const btn = document.querySelector(`#ingestCard .tab-btn[data-tab="${name}"]`);
  if (btn) btn.click();
}

async function searchLibrary() {
  const q = $("libraryQuery").value.trim();
  if (!q) return;
  const box = $("libraryResults");
  box.innerHTML = `<p class="field-hint">Searching the Internet Archive, Wikimedia Commons, YouTube (Creative Commons), Project Gutenberg and LibriVox...</p>`;
  try {
    const d = await api(`/api/library/search?q=${encodeURIComponent(q)}&kind=${encodeURIComponent($("libraryKind").value)}`);
    box.innerHTML = d.results.length ? "" : `<p class="field-hint">Nothing found for "${escapeHtml(q)}".</p>`;
    d.results.forEach((x) => box.appendChild(libraryRow(x)));
    if (d.errors.length) box.insertAdjacentHTML("beforeend", `<p class="field-hint">Some sources did not answer: ${escapeHtml(d.errors.join("; "))}</p>`);
    refreshIcons();
  } catch (e) {
    box.innerHTML = `<p class="rights-error">${escapeHtml(e.message)}</p>`;
  }
}

function libraryRow(x) {
  const row = document.createElement("div");
  row.className = "lib-row";
  const meta = [x.source, x.year, x.creator, x.duration_sec ? fmtTime(x.duration_sec) : x.runtime].filter(Boolean).map((v) => escapeHtml(v)).join(" · ");
  const reason = x.reason && x.reason !== x.license.label ? ` · ${escapeHtml(x.reason)}` : "";
  const video = x.kind === "film" || x.kind === "lecture";
  const action = x.gutenberg_id ? `<button class="btn btn-sm btn-primary" data-act="explain">Own-words summary</button>`
    : video ? `<button class="btn btn-sm btn-primary" data-act="use">Use this</button>` : "";
  row.innerHTML = `<div class="lib-main">${rightsChip(x.status)}<span class="lib-title">${escapeHtml(x.title)}</span>
      <span class="lib-meta">${meta}</span><span class="lib-reason">${escapeHtml(x.license.label)}${reason}</span></div>
    <div class="lib-actions">${action}<a class="btn btn-sm btn-outline" href="${escapeHtml(x.url)}" target="_blank" rel="noopener">Source</a></div>`;
  const btn = row.querySelector("button[data-act]");
  if (btn && btn.dataset.act === "use") btn.addEventListener("click", () => { switchTab("url"); $("videoUrl").value = x.url; checkLinkRights(x.url); });
  if (btn && btn.dataset.act === "explain") btn.addEventListener("click", () => {
    switchTab("explain");
    $("exSource").value = "gutenberg";
    onExSourceChange();
    $("exGutenbergId").value = x.gutenberg_id;
    $("exTitle").value = x.title;
    $("exAuthor").value = x.creator || "";
  });
  return row;
}

// ---------- own-words explainers ----------
function initExplainer() {
  $("exSource").addEventListener("change", onExSourceChange);
  $("exLanguage").addEventListener("change", fillExplainerVoices);
  $("exBasis").addEventListener("change", () => { $("exConfirmWrap").hidden = $("exBasis").value === "private"; });
  $("btnMakeExplainer").addEventListener("click", () => makeExplainer(explainerBody()));
  $("btnExplainFromJob").addEventListener("click", explainFromJob);
  $("btnAddToLibrary").addEventListener("click", addJobToLibrary);
  $("exAddToLibrary").addEventListener("click", addExplainerToLibrary);
  onExSourceChange();
}

// ---------- web library ----------
async function addJobToLibrary() {
  if (!state.jobId) return;
  let body = { kind: radioValue("contentMode", "movie") === "lecture" ? "lecture" : "movie", title: $("filmTitleInput").value.trim() || null };
  if (body.kind === "movie") {
    const series = prompt("Is this an episode of a series? Type the series name, or leave empty for a film.", "");
    if (series === null) return;
    if (series.trim()) {
      body = { ...body, kind: "series", series: series.trim(), episode: parseInt(prompt("Episode number:", "1") || "1", 10) || 1 };
    }
  }
  try {
    const d = await api(`/api/studio/library/job/${state.jobId}`, { json: body });
    alert(`Added "${d.title.title}" to the web library (${d.title.minutes} min). It is stored as a recipe: source link, clip times and narration text.`);
  } catch (e) {
    alert(`Could not add to the library: ${e.message}`);
  }
}

async function addExplainerToLibrary() {
  if (!state.explainerId) return;
  const kind = $("exKind").value;
  try {
    const d = await api(`/api/studio/library/explainer/${state.explainerId}`, { json: { kind } });
    alert(`Added "${d.title.title}" to the web library. It is stored as its script; the voice is made again when someone plays it.`);
  } catch (e) {
    alert(`Could not add to the library: ${e.message}`);
  }
}

function onExSourceChange() {
  const s = $("exSource").value;
  $("exGutenbergRow").hidden = s !== "gutenberg";
  $("exFileRow").hidden = s !== "file";
  $("exTextRow").hidden = s !== "text";
  $("exRightsRow").hidden = s === "gutenberg";
}

function fillExplainerVoices() {
  const h = state.health || {};
  const lang = $("exLanguage").value;
  const sel = $("exVoice");
  sel.innerHTML = "";
  Object.entries(h.voices || {}).forEach(([key, v]) => {
    if (v.ready === false || v.language !== lang) return;
    const o = document.createElement("option");
    o.value = key;
    o.textContent = v.name;
    sel.appendChild(o);
  });
  sel.value = (h.default_voices || {})[lang] || (sel.options[0] ? sel.options[0].value : "");
}

function explainerBody() {
  const s = $("exSource").value;
  const body = { source: s, kind: $("exKind").value, target_minutes: parseFloat($("exMinutes").value), language: $("exLanguage").value,
    voice: $("exVoice").value || null, title: $("exTitle").value.trim() || null, author: $("exAuthor").value.trim() || null };
  if (s === "gutenberg") {
    const m = String($("exGutenbergId").value).match(/(\d+)/);
    body.gutenberg_id = m ? parseInt(m[1], 10) : null;
  }
  if (s === "file") body.file_path = $("exFilePath").value.trim();
  if (s === "text") body.text = $("exText").value;
  if (s !== "gutenberg" && $("exBasis").value !== "private") body.rights_basis = $("exBasis").value;
  return body;
}

async function makeExplainer(body) {
  if (body.source === "gutenberg" && !body.gutenberg_id) { alert("Enter a Project Gutenberg book number or link."); return; }
  if (body.rights_basis && !$("exConfirm").checked) { alert(DECLARE_CONFIRM); return; }
  showProgressModal("Creating an own-words summary...", "Getting the text...", 2);
  try {
    const { task_id } = await api("/api/explainer", { json: body });
    const t = await pollTask(task_id, (x) => updateProgressModal(x.message, x.progress), 2000);
    hideProgressModal();
    showExplainer(t.result, task_id);
  } catch (e) {
    hideProgressModal();
    alert(`Explainer error: ${e.message}`);
  }
}

function explainFromJob() {
  if (!state.jobId) return;
  const lecture = radioValue("contentMode", "movie") === "lecture";
  const lang = $("languageSelect").value === "Hindi" ? "Hindi" : "English";
  makeExplainer({ source: "job", job_id: state.jobId, kind: lecture ? "lecture" : "story", language: lang, voice: null,
    target_minutes: Math.min(30, Math.max(3, parseInt($("durationSlider").value, 10) || 10)),
    title: $("filmTitleInput").value.trim() || null });
}

function showExplainer(r, id) {
  state.explainerId = id;
  const card = $("explainerCard");
  card.hidden = false;
  card.style.display = "";
  $("exResultTitle").textContent = r.title || "Own-words summary";
  $("exResultMeta").textContent = (r.rights || {}).attribution || "";
  const facts = [
    r.minutes ? `${r.minutes} min video` : null,
    `${r.words} words (aimed at ${r.target_words})`,
    r.originality_pct != null ? `${r.originality_pct}% new wording` : r.originality_note,
    `read in ${r.parts} part(s)`,
    r.rewritten_sentences ? `${r.rewritten_sentences} sentence(s) rewritten` : null,
    `rights: ${RIGHTS_LABELS[(r.rights || {}).status] || "unknown"}`
  ].filter(Boolean);
  $("exFacts").innerHTML = facts.map((f) => `<span class="stat-pill">${escapeHtml(f)}</span>`).join("");
  $("exThesis").textContent = r.thesis || "";
  $("exTakeaways").innerHTML = (r.takeaways || []).map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  $("exQuiz").innerHTML = (r.quiz || []).map((q) => `<li>${escapeHtml(q.q)}<details><summary>Answer</summary>${escapeHtml(q.a)}</details></li>`).join("");
  $("exScript").innerHTML = [`<p>${escapeHtml(r.intro || "")}</p>`,
    ...(r.sections || []).map((s) => `<h4>${escapeHtml(s.heading || "")}</h4><p>${escapeHtml(s.narration || "")}</p>`)].join("");
  const player = $("explainerPlayer");
  if (r.video_url) player.src = `${r.video_url}?t=${Date.now()}`;
  else player.removeAttribute("src");
  if (r.private) player.setAttribute("controlsList", "nodownload");
  else player.removeAttribute("controlsList");
  if (!r.private) {
    $("exDlVideo").href = `${r.video_url}?download=true`;
    $("exDlScript").href = `/api/explainer/${id}/file/script`;
    $("exDlSrt").href = `/api/explainer/${id}/file/srt`;
    $("exDlNotes").href = `/api/explainer/${id}/file/notes`;
  }
  applyPrivate(r.private ? { id, kind: "explainer", expiresIn: null } : null, ["explainerCard"]);
  refreshIcons();
  card.scrollIntoView({ behavior: "smooth" });
}
