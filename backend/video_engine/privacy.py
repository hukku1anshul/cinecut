"""
Private viewing: a summary of material without a verified licence is shown in the app and then deleted.

- private_scope() marks the running thread as private, and the story, scene and analysis caches check
  cache_allowed() before writing, so nothing learned about the work stays on disk.
- Every private job is written to temp/private_registry.json with the paths to delete: its workspace, a source
  that CineCut downloaded for it (never a file the user pointed to on their own disk) and anything rendered.
  The registry survives a crash, so leftovers are deleted at the next start.
- A private job is deleted when the user presses "Delete now", 60 minutes after it was last opened
  (CINECUT_PRIVATE_TTL_MIN), or when CineCut starts.
- As a safety net the purge also removes cache files written while the job ran.
"""
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend.config import TEMP_DIR

TTL_SEC = int(os.environ.get("CINECUT_PRIVATE_TTL_MIN", "60")) * 60
REGISTRY = TEMP_DIR / "private_registry.json"
CACHE_DIRS = ("story_cache", "visual_cache", "analysis_cache", "transcripts")
_local = threading.local()
_lock = threading.RLock()
DELETED: Dict[str, float] = {}       # job id -> when it was deleted (for a clear message instead of "not found")


def cache_allowed() -> bool:
    return not getattr(_local, "private", False)


@contextmanager
def private_scope(active: bool = True):
    prev = getattr(_local, "private", False)
    _local.private = bool(active) or prev
    try:
        yield
    finally:
        _local.private = prev


def _load() -> Dict[str, Any]:
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(reg: Dict[str, Any]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1), encoding="utf-8")
    os.replace(tmp, REGISTRY)


def register(job_id: str, paths: Iterable[str] = (), sweep_caches: bool = True) -> None:
    """sweep_caches=False: the entry holds only its own paths (a finished short version waiting to be watched), so its
    deletion must not sweep the shared caches that other jobs wrote while it waited."""
    with _lock:
        reg = _load()
        now = time.time()
        entry = reg.setdefault(job_id, {"paths": [], "started": now, "last_seen": now})
        entry["paths"] = sorted(set(entry["paths"]) | {str(p) for p in paths if p})
        if not sweep_caches:
            entry["no_cache_sweep"] = True
        entry["owner"] = os.getpid()          # the process holding it: start-up clean-up leaves live processes' items alone
        _save(reg)


def add_paths(job_id: str, *paths: Optional[str]) -> None:
    if any(paths):
        register(job_id, [p for p in paths if p])


def is_registered(job_id: str) -> bool:
    return job_id in _load()


def touch(job_id: str) -> None:
    with _lock:
        reg = _load()
        if job_id in reg:
            reg[job_id]["last_seen"] = time.time()
            _save(reg)


def expires_in(job_id: str) -> Optional[int]:
    entry = _load().get(job_id)
    return None if not entry else max(0, int(entry["last_seen"] + TTL_SEC - time.time()))


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0


def _remove(p: Path) -> int:
    try:
        if not p.exists():
            return 0
        size = _size(p)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        return size
    except OSError:
        return 0


def drop_paths(job_id: str, paths: Iterable[str]) -> None:
    """Stops a job from owning these paths (a newer private job took them over)."""
    drop = {str(p) for p in paths}
    with _lock:
        reg = _load()
        if job_id in reg and drop:
            reg[job_id]["paths"] = [p for p in reg[job_id]["paths"] if p not in drop]
            _save(reg)


def forget(job_id: Optional[str]) -> None:
    """Drops a registry entry without deleting its files (used when a job takes over a private download)."""
    if not job_id:
        return
    with _lock:
        reg = _load()
        if reg.pop(job_id, None) is not None:
            _save(reg)


def _stores(jobs: Any) -> List[Dict[str, Any]]:
    if jobs is None:
        return []
    return [jobs] if isinstance(jobs, dict) else list(jobs)


def purge(job_id: str, jobs: Any = None, reason: str = "deleted") -> Dict[str, Any]:
    """Deletes everything a private job produced and forgets it. jobs: one dict of jobs or a list of them."""
    with _lock:
        reg = _load()
        entry = reg.pop(job_id, None)
        _save(reg)
    freed, removed, leftovers = 0, 0, []
    if entry:
        for raw in entry["paths"]:
            n = _remove(Path(raw))
            freed += n
            removed += 1 if n else 0
            if Path(raw).exists():
                leftovers.append(raw)
    if entry and not entry.get("retry") and not entry.get("no_cache_sweep"):
        started = entry.get("started", time.time()) - 5
        for sub in CACHE_DIRS:
            d = TEMP_DIR / sub
            if not d.exists():
                continue
            for f in d.iterdir():
                try:
                    if f.stat().st_mtime >= started:
                        freed += _remove(f)
                        removed += 1
                except OSError:
                    continue
    if not job_id.startswith("retry_"):
        freed += _remove(TEMP_DIR / job_id)
        if (TEMP_DIR / job_id).exists():
            leftovers.append(str(TEMP_DIR / job_id))
    if leftovers:
        _retry_later(leftovers)
    for store in _stores(jobs):
        store.pop(job_id, None)
    try:
        from backend.video_engine import gap_narrator
        gap_narrator._ENGLISH_CACHE.clear()      # narration text lives in memory too
    except Exception:
        pass
    DELETED[job_id] = time.time()
    return {"job_id": job_id, "removed_items": removed, "freed_bytes": freed, "reason": reason, "retrying": len(leftovers)}


def _retry_later(paths: List[str]) -> None:
    """Windows keeps a file locked while a video is still streaming from it; the next sweep tries again."""
    with _lock:
        reg = _load()
        reg[f"retry_{int(time.time() * 1000)}"] = {"paths": sorted(set(paths)), "started": time.time(), "last_seen": 0, "retry": True}
        _save(reg)


def sweep(jobs: Any = None) -> int:
    now = time.time()
    expired = [jid for jid, e in _load().items() if e["last_seen"] + TTL_SEC < now]
    for jid in expired:
        job = next((s[jid] for s in _stores(jobs) if jid in s), {}) or {}
        if job.get("status") in ("analyzing", "rendering", "running"):
            continue
        purge(jid, jobs, "expired")
    return len(expired)


def _alive(pid: Any) -> bool:
    """Whether a process is still running (without signalling it: on Windows os.kill would end it)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def purge_leftovers(jobs: Any = None) -> int:
    """At start-up nothing from the last session is in memory, so every private job left by a process that has ended
    is deleted. Jobs held by another process that is still running (a script, a test, a second worker) are left to it
    and to the expiry sweep, so a restart never deletes someone's private work in the middle."""
    ids = [jid for jid, e in _load().items() if not (isinstance(e, dict) and _alive(e.get("owner")))]
    for jid in ids:
        purge(jid, jobs, "left over from the last session")
    return len(ids)


def start_sweeper(jobs: Any, every_sec: int = 120) -> None:
    def loop():
        while True:
            time.sleep(every_sec)
            try:
                sweep(jobs)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True, name="private-sweeper").start()
