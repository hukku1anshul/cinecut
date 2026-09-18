import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

from backend.config import TEMP_DIR
from backend.video_engine.probe import get_video_metadata, format_seconds, extract_subtitles_to_srt
from backend.video_engine.subtitle_parser import group_dialogue_into_scenes, score_dialogue_significance, parse_srt_file
from backend.video_engine.summarizer import compute_local_heuristic_summary
from backend.video_engine.renderer import render_summary_video

SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
REGISTRY_FILE = TEMP_DIR / "watchfolder_registry.json"
logger = logging.getLogger("CineCutWatchfolder")


class WatchfolderDaemon:
    """Watches a media folder and writes a companion '<Movie> - Recap.mp4' for each new, fully written film."""

    def __init__(self, watch_dir: str, output_dir: Optional[str] = None, target_minutes: int = 15,
                 render_mode: str = "stream_copy", check_interval_sec: int = 5):
        self.watch_dir = Path(watch_dir.strip().strip('"')).resolve()
        self.output_dir = Path(output_dir.strip().strip('"')).resolve() if output_dir else None
        self.target_minutes = target_minutes
        self.render_mode = render_mode
        self.check_interval_sec = max(2, check_interval_sec)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()          # protects logs and registry
        self._scan_lock = threading.Lock()     # only one scan processes files at a time
        self._candidate_sizes: Dict[str, tuple] = {}
        self.logs: List[Dict[str, Any]] = []
        self.registry: Dict[str, Any] = self._load_registry()

    def _load_registry(self) -> Dict[str, Any]:
        try:
            if REGISTRY_FILE.exists():
                return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_registry(self) -> None:
        try:
            with self._lock:
                REGISTRY_FILE.write_text(json.dumps(self.registry, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save watchfolder registry: {e}")

    def log_event(self, message: str, level: str = "info") -> None:
        entry = {"timestamp": time.strftime("%H:%M:%S"), "message": message, "level": level}
        with self._lock:
            self.logs.append(entry)
            del self.logs[:-100]
        logger.info(message)

    def start(self) -> None:
        if self._running:
            return
        if not self.watch_dir.is_dir():
            raise FileNotFoundError(f"Watch directory does not exist: {self.watch_dir}")
        self._running = True
        self.log_event(f"Watchfolder started. Monitoring: {self.watch_dir}")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.log_event("Watchfolder stopped.")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_running(self) -> bool:
        return self._running

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.scan_once()
            except Exception as e:
                self.log_event(f"Error during scan: {e}", level="error")
            time.sleep(self.check_interval_sec)

    def scan_once(self) -> List[str]:
        """Scans the folder; processes files whose size has been stable. Returns newly processed paths."""
        if not self.watch_dir.exists() or not self._scan_lock.acquire(blocking=False):
            return []
        processed = []
        try:
            for root, _, files in os.walk(self.watch_dir):
                for filename in files:
                    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    if "_Recap" in filename or " - Recap" in filename or filename.endswith((".part", ".crdownload", ".tmp")):
                        continue
                    full_path = Path(root) / filename
                    path_str = str(full_path.resolve())
                    try:
                        st = full_path.stat()
                    except OSError:
                        continue
                    if st.st_size < 100_000:
                        continue
                    signature = f"{st.st_size}|{int(st.st_mtime)}"
                    entry = self.registry.get(path_str)
                    if entry and entry.get("signature") == signature and entry.get("status") in ("completed", "skipped", "error"):
                        continue  # this exact file version was already handled (errors retry only if the file changes)

                    now = time.time()
                    last = self._candidate_sizes.get(path_str)
                    if last is None or last[0] != st.st_size:
                        if last is None:
                            self.log_event(f"Detected {filename} ({st.st_size / 1048576:.1f} MB). Waiting for the write to finish...")
                        self._candidate_sizes[path_str] = (st.st_size, now)
                        continue
                    if now - last[1] < 3.0:
                        continue
                    del self._candidate_sizes[path_str]
                    self.log_event(f"File stable. Creating a {self.target_minutes}-minute recap of {filename}...")
                    if self._process_video_file(path_str, signature):
                        processed.append(path_str)
        finally:
            self._scan_lock.release()
        return processed

    def _process_video_file(self, video_path: str, signature: str) -> bool:
        src = Path(video_path)
        dest_dir = self.output_dir or src.parent
        recap_filename = f"{src.stem} - Recap.mp4"
        job_id = f"watch_{uuid.uuid4().hex[:8]}"
        self.registry[video_path] = {"status": "processing", "signature": signature, "started_at": time.time()}
        self._save_registry()
        try:
            meta = get_video_metadata(video_path)
            dur = meta.get("duration_sec", 0.0)
            if dur <= self.target_minutes * 60.0 * 1.2:
                self.log_event(f"Skipping {src.name}: it is already close to or shorter than the recap length.", level="warning")
                self.registry[video_path] = {"status": "skipped", "signature": signature}
                self._save_registry()
                return False

            sub_temp = TEMP_DIR / job_id / "subs.srt"
            subs = parse_srt_file(str(sub_temp)) if extract_subtitles_to_srt(video_path, str(sub_temp)) else []
            candidates = group_dialogue_into_scenes(subs) if subs else []
            for c in candidates:
                c["dialogue_score"] = score_dialogue_significance(c.get("dialogue", ""), c.get("duration", 0.0))

            from backend.video_engine.audio_analyzer import analyze_audio_track
            from backend.video_engine.essence_engine import extract_film_canonical_essence
            silences = analyze_audio_track(video_path, timeout=max(300.0, dur * 0.5))["silences"]
            milestones = extract_film_canonical_essence(src.stem, dur)["milestones"]

            curated = compute_local_heuristic_summary(dur, self.target_minutes * 60.0, candidates, [], silences,
                                                      "story_focused", "full_cut", essence_milestones=milestones)
            out_file = render_summary_video(source_video_path=video_path, scenes=curated, job_id=job_id,
                                            output_filename=recap_filename, render_mode=self.render_mode,
                                            include_voiceover=False, normalize_audio=False,
                                            output_dir=str(dest_dir))
            total = sum(s["duration"] for s in curated)
            self.registry[video_path] = {"status": "completed", "signature": signature, "completed_at": time.time(),
                                         "duration": format_seconds(dur), "recap_path": out_file,
                                         "recap_filename": Path(out_file).name}
            self._save_registry()
            self.log_event(f"Created {Path(out_file).name} ({format_seconds(total)}).", level="success")
            return True
        except Exception as e:
            self.log_event(f"Failed to create a recap for {src.name}: {e}", level="error")
            self.registry[video_path] = {"status": "error", "signature": signature, "error": str(e), "failed_at": time.time()}
            self._save_registry()
            return False

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            completed = [{"source": k, **v} for k, v in self.registry.items() if v.get("status") == "completed"]
            return {"running": self._running, "watch_dir": str(self.watch_dir),
                    "output_dir": str(self.output_dir) if self.output_dir else "Same as Media Source",
                    "target_minutes": self.target_minutes, "render_mode": self.render_mode,
                    "completed_count": len(completed), "completed_items": completed[-10:],
                    "recent_logs": list(self.logs[-20:])}


WATCHFOLDER_DAEMON: Optional[WatchfolderDaemon] = None


def get_watchfolder_daemon() -> Optional[WatchfolderDaemon]:
    return WATCHFOLDER_DAEMON


def set_watchfolder_daemon(daemon: Optional[WatchfolderDaemon]) -> None:
    global WATCHFOLDER_DAEMON
    WATCHFOLDER_DAEMON = daemon
