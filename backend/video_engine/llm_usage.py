"""
Free-tier guard for cloud AI keys. Every key is tracked per model against its per-minute, per-hour and per-day
limits, and is used again as soon as the window that stopped it rolls over.

- Keys come from .env: GEMINI_API_KEY, GEMINI_API_KEY_2, ..., GROQ_API_KEY[_n], NVIDIA_API_KEY[_n],
  CLOUDFLARE_API_KEY[_n] + CLOUDFLARE_ACCOUNT_ID[_n].
- Free tiers limit each key (for Google: each project) per model. LIMITS holds the free-tier request limits per
  minute / hour / day; CINECUT_<PROVIDER>_DAILY_CAP additionally caps a whole key per day if you set it.
- A 429 reply says which limit was hit. A per-minute or per-hour limit rests only that key and model for the time
  the provider asks (usually under a minute); a per-day limit rests it until the provider's day resets (midnight
  Pacific time for Gemini, midnight UTC for the others). Nothing else about the key is blocked.
- wait_hint() tells callers how long until the next key or model frees up, so a short wait on a cloud model can
  be preferred over a slow local fallback.
Note: counting cannot see billing. A key is only guaranteed free if its account has no billing attached.
"""
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from backend.config import TEMP_DIR

USAGE_FILE = TEMP_DIR / "llm_usage.json"
# (per minute, per hour, per day), None = no limit. The first pattern contained in the model name wins.
LIMITS: Dict[str, List[Tuple[str, Tuple[Optional[int], Optional[int], Optional[int]]]]] = {
    "gemini": [("tts", (3, None, 10)), ("flash-lite", (15, None, 1000)), ("gemma", (30, None, 14400)),
               ("pro", (5, None, 100)), ("flash", (10, None, 20)), ("", (10, None, 250))],   # 3.x Flash: 20 a day (Google's reply, Sep 2026)
    "gemini_tts": [("", (3, None, 10))],
    "groq": [("8b", (30, None, 14400)), ("", (30, None, 1000))],
    "groq_audio": [("", (20, None, 2000))],
    "cloudflare": [("", (None, None, 30))],       # 10,000 neurons a day, about 30 large requests
    "nvidia": [("", (40, None, 1000))],
    "openrouter": [("", (20, None, 1000))],       # free models: 20 a minute, 1,000 a day per account
}
# Limits on units other than requests, per key and model: Groq Whisper counts audio seconds per hour and per day.
UNIT_LIMITS: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = {"groq_audio": (None, 7200.0, 28800.0)}
PACIFIC_DAY = {"gemini", "gemini_tts"}
_lock = threading.RLock()
_state: Optional[Dict] = None


# ------------------------------------------------------------------ keys
def env_keys(prefix: str) -> List[str]:
    pattern = re.compile(rf"^{prefix}_API_KEY(?:_(\d+))?$")
    found = []
    for name, value in os.environ.items():
        m = pattern.match(name)
        if m and value.strip():
            found.append((int(m.group(1) or 1), value.strip()))
    seen, out = set(), []
    for _, v in sorted(found):
        if v not in seen:          # the same key listed twice is one key
            seen.add(v)
            out.append(v)
    return out


def cloudflare_accounts() -> List[Tuple[str, str]]:
    pairs = []
    for name, value in sorted(os.environ.items()):
        m = re.match(r"^CLOUDFLARE_API_KEY(_\d+)?$", name)
        if m and value.strip():
            account = os.environ.get("CLOUDFLARE_ACCOUNT_ID" + (m.group(1) or ""), "").strip()
            if account:
                pairs.append((value.strip(), account))
    return pairs


def key_id(provider: str, key: str) -> str:
    return f"{provider}:{hashlib.sha1(key.encode()).hexdigest()[:8]}"


# ------------------------------------------------------------------ clocks
def _pacific(utc: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:          # no tz database on this PC: US daylight time rules by hand
        y = utc.year

        def sunday(month: int, n: int) -> datetime:
            d = datetime(y, month, 1, tzinfo=timezone.utc)
            return d + timedelta(days=(6 - d.weekday()) % 7, weeks=n - 1)
        dst = sunday(3, 2) + timedelta(hours=10) <= utc < sunday(11, 1) + timedelta(hours=9)
        return utc + timedelta(hours=-7 if dst else -8)


def _local_now(provider: str) -> datetime:
    utc = datetime.now(timezone.utc)
    return _pacific(utc) if provider in PACIFIC_DAY else utc


def day_of(provider: str) -> str:
    return _local_now(provider).date().isoformat()


def seconds_to_reset(provider: str) -> float:
    now = _local_now(provider)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return max(60.0, (nxt - now).total_seconds())


# ------------------------------------------------------------------ state
def limits_for(provider: str, model: Optional[str]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    name = (model or "").lower()
    for pattern, lim in LIMITS.get(provider, [("", (None, None, None))]):
        if pattern in name:
            per_min, per_hour, per_day = lim
            break
    else:
        per_min, per_hour, per_day = None, None, None
    if model is None:                 # the key as a whole: only the optional daily cap applies
        per_min, per_hour = None, None
        try:
            per_day = int(os.environ[f"CINECUT_{provider.upper()}_DAILY_CAP"])
        except (KeyError, ValueError):
            per_day = None
    return per_min, per_hour, per_day


def _load() -> Dict:
    global _state
    if _state is None:
        try:
            _state = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _state = {}
        if "slots" not in _state:
            _state = {"slots": {}}
    return _state


def _save() -> None:
    try:
        tmp = USAGE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state), encoding="utf-8")
        os.replace(tmp, USAGE_FILE)
    except OSError:
        pass


def _slot(provider: str, key: str, model: Optional[str]) -> Tuple[str, Dict]:
    name = f"{key_id(provider, key)}|{model or '*'}"
    slots = _load()["slots"]
    s = slots.setdefault(name, {"day": day_of(provider), "n": 0, "recent": [], "blocked": 0.0, "why": ""})
    s.setdefault("units_day", 0.0)
    s.setdefault("units_recent", [])
    today = day_of(provider)
    if s["day"] != today:            # the provider's day rolled over
        s.update({"day": today, "n": 0, "units_day": 0.0})
        if s.get("why") == "day":
            s.update({"blocked": 0.0, "why": ""})
    cutoff = time.time() - 3600
    s["recent"] = [t for t in s["recent"] if t > cutoff]
    s["units_recent"] = [u for u in s["units_recent"] if u[0] > cutoff]
    return name, s


def _free_in(provider: str, s: Dict, lim: Tuple[Optional[int], Optional[int], Optional[int]], units: float = 0.0,
             model: Optional[str] = None) -> float:
    """Seconds until this slot may be used (0 = now), including room for `units` more (e.g. audio seconds)."""
    now = time.time()
    wait = max(0.0, s.get("blocked", 0.0) - now)
    per_min, per_hour, per_day = lim
    if per_day is not None and s["n"] >= per_day:
        wait = max(wait, seconds_to_reset(provider))
    for window, cap in ((60, per_min), (3600, per_hour)):
        if cap is None:
            continue
        inside = sorted(t for t in s["recent"] if t > now - window)
        if len(inside) >= cap:
            wait = max(wait, inside[len(inside) - cap] + window - now + 0.5)
    if model is not None and provider in UNIT_LIMITS:
        _, u_hour, u_day = UNIT_LIMITS[provider]
        if u_day is not None and s["units_day"] + units > u_day:
            wait = max(wait, seconds_to_reset(provider))
        if u_hour is not None:
            recent = sorted(s["units_recent"])
            used = sum(u for _, u in recent)
            for t, u in recent:                   # oldest units leave the hour window first
                if used + units <= u_hour:
                    break
                used -= u
                wait = max(wait, t + 3600 - now + 0.5)
    return wait


def available(provider: str, key: str, model: Optional[str] = None, units: float = 0.0) -> bool:
    with _lock:
        _, whole = _slot(provider, key, None)
        if _free_in(provider, whole, limits_for(provider, None)) > 0:
            return False
        if model is None:
            return True
        _, s = _slot(provider, key, model)
        return _free_in(provider, s, limits_for(provider, model), units, model) <= 0


def free_in(provider: str, key: str, model: Optional[str] = None, units: float = 0.0) -> float:
    with _lock:
        _, whole = _slot(provider, key, None)
        wait = _free_in(provider, whole, limits_for(provider, None))
        if model is not None:
            _, s = _slot(provider, key, model)
            wait = max(wait, _free_in(provider, s, limits_for(provider, model), units, model))
        return wait


def wait_hint(provider: str, keys: Iterable[str], models: Iterable[Optional[str]] = (None,), units: float = 0.0) -> Optional[float]:
    """Shortest wait until any of these keys and models can be used, or None if there are none."""
    waits = [free_in(provider, k, m, units) for k in keys for m in (list(models) or [None])]
    return min(waits) if waits else None


def record(provider: str, key: str, amount: int = 1, model: Optional[str] = None, units: float = 0.0) -> None:
    now = time.time()
    with _lock:
        for m in ([model] if model else []) + [None]:
            _, s = _slot(provider, key, m)
            s["n"] += amount
            s["recent"].extend([now] * amount)
            if units:
                s["units_day"] += units
                s["units_recent"].append([now, units])
        _save()


def cool_down(provider: str, key: str, seconds: float, model: Optional[str] = None, why: str = "rest") -> None:
    with _lock:
        _, s = _slot(provider, key, model)
        s["blocked"] = max(s.get("blocked", 0.0), time.time() + max(5.0, seconds))
        s["why"] = why
        _save()


def clear(provider: str, key: str, model: Optional[str] = None) -> None:
    """Lifts a rest early: the key and model just answered, so the estimate of when its limit lifts was too long."""
    with _lock:
        _, s = _slot(provider, key, model)
        s["blocked"], s["why"] = 0.0, ""
        _save()


def note_429(provider: str, key: str, model: Optional[str], message: str, retry_after: Optional[float] = None) -> float:
    """Reads which limit a 429 reply refers to and rests only that key and model for as long as needed."""
    text = message or ""
    if re.search(r"per[ _-]?day|daily|RPD|requests_per_day|free_tier_requests", text, re.I):     # OpenRouter says "per-day"
        secs, why = seconds_to_reset(provider), "day"
    elif re.search(r"per ?hour|perhour|hourly", text, re.I):
        secs, why = max(retry_after or 0.0, 300.0), "hour"
    else:
        secs, why = (retry_after if retry_after and retry_after > 0 else 60.0), "minute"
    cool_down(provider, key, secs, model, why)
    return secs


def summary() -> Dict[str, Dict]:
    keys = {"gemini": env_keys("GEMINI") + [k for k in env_keys("GOOGLE") if k not in env_keys("GEMINI")],
            "groq": env_keys("GROQ"), "nvidia": env_keys("NVIDIA"), "cloudflare": [k for k, _ in cloudflare_accounts()],
            "openrouter": env_keys("OPENROUTER")}
    out: Dict[str, Dict] = {}
    with _lock:
        slots = _load()["slots"]
        for provider, ks in keys.items():
            if not ks:
                continue
            ids = {key_id(provider, k) for k in ks}
            used = sum(s["n"] for name, s in slots.items() if name.split("|")[0] in ids and name.endswith("|*")
                       and s["day"] == day_of(provider))
            resting = [{"model": name.split("|")[1], "why": s.get("why"), "free_in_sec": int(s["blocked"] - time.time())}
                       for name, s in slots.items() if name.split("|")[0] in ids and s.get("blocked", 0) > time.time()]
            ready = sum(1 for k in ks if available(provider, k))
            out[provider] = {"keys": len(ks), "keys_ready": ready, "used_today": used, "resting": resting[:12],
                             "day_resets_in_min": int(seconds_to_reset(provider) // 60)}
    return out
