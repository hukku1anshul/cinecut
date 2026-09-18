// Runs in the page's own JavaScript world so it can use the official player's seek function.
// Netflix rejects direct changes to <video>.currentTime, so its player API is used when present.
(function () {
  function mainVideo() {
    const vids = Array.from(document.querySelectorAll("video"));
    return vids.sort((a, b) => (b.duration || 0) - (a.duration || 0))[0] || null;
  }
  window.addEventListener("message", (event) => {
    if (event.source !== window || !event.data || event.data.cinecut !== "seek") return;
    const sec = Number(event.data.sec);
    if (!isFinite(sec) || sec < 0) return;
    try {
      const nf = window.netflix && window.netflix.appContext && window.netflix.appContext.state.playerApp.getAPI().videoPlayer;
      if (nf) {
        const ids = nf.getAllPlayerSessionIds();
        nf.getVideoPlayerBySessionId(ids[ids.length - 1]).seek(Math.round(sec * 1000));
        return;
      }
    } catch (e) { /* fall back to the video element */ }
    const v = mainVideo();
    if (v) v.currentTime = sec;
  });
})();
