"""
Gemini REST client (free tier friendly).

- Key sent in the `x-goog-api-key` header, never in the URL.
- Free-tier quotas are per model per key (e.g. only 20 requests a day for the newest Flash), so each key
  walks through a chain of free models - newest Flash, older Flash, Flash-Lite, Gemma - and a model that
  is out of quota is skipped until its quota resets.
- Rotates across all configured keys (GEMINI_API_KEY, GEMINI_API_KEY_2, ...) and respects llm_usage caps.
- CINECUT_GEMINI_MODEL forces one model.
"""
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from backend.config import DEFAULT_GEMINI_MODEL, GEMINI_API_BASE
from backend.video_engine import llm_usage

MODEL_CHAIN = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest",
               "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash-lite",
               "gemma-4-31b-it", "gemma-4-26b-a4b-it")
_available: Dict[str, List[str]] = {}
_blocked: Dict[str, float] = {}          # "keyid|model" -> time when it may be tried again
_LOCK = threading.Lock()


class GeminiError(RuntimeError):
    pass


class GeminiRateLimited(GeminiError):
    def __init__(self, message: str, retry_after: float = 60.0):
        super().__init__(message)
        self.retry_after = retry_after


def list_models(api_key: str, timeout: float = 15.0) -> List[Dict[str, Any]]:
    models: List[Dict[str, Any]] = []
    page_token = None
    with httpx.Client(timeout=timeout) as client:
        for _ in range(5):
            params: Dict[str, Any] = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            res = client.get(GEMINI_API_BASE, params=params, headers={"x-goog-api-key": api_key})
            if res.status_code != 200:
                raise GeminiError(_error_message(res))
            data = res.json()
            models.extend(data.get("models", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return models


def models_for(api_key: str) -> List[str]:
    """Free models to try for this key, best first (only ones the key can actually use)."""
    override = os.environ.get("CINECUT_GEMINI_MODEL")
    if override:
        return [override]
    kid = llm_usage.key_id("gemini", api_key)
    with _LOCK:
        if kid in _available:
            return _available[kid]
    try:
        ids = {m.get("name", "").split("/")[-1] for m in list_models(api_key)
               if "generateContent" in m.get("supportedGenerationMethods", [])}
        chain = [m for m in MODEL_CHAIN if m in ids] or [DEFAULT_GEMINI_MODEL]
    except Exception:
        chain = list(MODEL_CHAIN[:1]) + [m for m in MODEL_CHAIN if "lite" in m or "gemma" in m]
    with _LOCK:
        _available[kid] = chain
    return chain


def resolve_model(api_key: str) -> str:
    """The first model in this key's chain that is not currently out of quota."""
    kid = llm_usage.key_id("gemini", api_key)
    now = time.time()
    for m in models_for(api_key):
        if _blocked.get(f"{kid}|{m}", 0) <= now and llm_usage.available("gemini", api_key, m):
            return m
    return models_for(api_key)[0]


def _error_message(res: httpx.Response) -> str:
    try:
        return f"Gemini API error ({res.status_code}): {res.json().get('error', {}).get('message', res.text[:300])}"
    except Exception:
        return f"Gemini API error ({res.status_code}): {res.text[:300]}"


def _retry_after(res: httpx.Response) -> float:
    try:
        for detail in res.json().get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if delay:
                return float(str(delay).rstrip("s"))
    except Exception:
        pass
    try:
        return float(res.headers.get("retry-after", 60))
    except ValueError:
        return 60.0


def _parse_loose(text: str) -> Any:
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise GeminiError("Gemini returned invalid JSON.")


def _request(api_key: str, model_id: str, prompt: str, system: Optional[str], temperature: float, timeout: float) -> httpx.Response:
    gemma = model_id.startswith("gemma")
    text = f"{system}\n\n{prompt}" if (system and gemma) else prompt
    payload: Dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                               "generationConfig": {"temperature": temperature}}
    if not gemma:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
    return httpx.post(f"{GEMINI_API_BASE}/{model_id}:generateContent", json=payload,
                      headers={"x-goog-api-key": api_key}, timeout=timeout)


def call_once(api_key: str, prompt: str, system: Optional[str] = None, temperature: float = 0.2,
              timeout: float = 90.0, model: Optional[str] = None) -> Any:
    """
    One key, walking its free model chain. Each model is used only while its per-minute and per-day free limits
    allow (llm_usage); a 429 rests just that model for as long as Google says (until midnight Pacific for a
    daily limit), an overload for 20 s. Raises GeminiRateLimited, carrying the wait until the next model on
    this key frees up, only when every model is unavailable.
    """
    kid = llm_usage.key_id("gemini", api_key)
    chain = [model] if model else models_for(api_key)
    last = "no model available"
    for model_id in chain:
        slot = f"{kid}|{model_id}"
        if _blocked.get(slot, 0) > time.time() or not llm_usage.available("gemini", api_key, model_id):
            continue
        res = _request(api_key, model_id, prompt, system, temperature, timeout)
        body = res.text[:1500]
        if res.status_code == 429 or "RESOURCE_EXHAUSTED" in body:
            llm_usage.note_429("gemini", api_key, model_id, body, _retry_after(res))
            last = _error_message(res)
            continue
        if res.status_code in (500, 502, 503, 504):
            _blocked[slot] = time.time() + 20
            last = _error_message(res)
            continue
        if res.status_code in (400, 404) and ("not found" in body.lower() or "not supported" in body.lower()
                                               or "not enabled" in body.lower() or "no longer available" in body.lower()):
            _blocked[slot] = time.time() + 24 * 3600
            last = _error_message(res)
            continue
        if res.status_code != 200:
            raise GeminiError(_error_message(res))
        llm_usage.record("gemini", api_key, model=model_id)
        data = res.json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates returned")
            raise GeminiError(f"Gemini returned no answer ({reason}).")
        parts = candidates[0].get("content", {}).get("parts", [])
        return _parse_loose("".join(p.get("text", "") for p in parts if not p.get("thought")))
    wait = llm_usage.wait_hint("gemini", [api_key], chain)
    raise GeminiRateLimited(f"All free Gemini models on this key are busy or out of quota ({last[:160]})", wait or 60.0)


def all_keys(first: Optional[str] = None) -> List[str]:
    keys = [first] if first else []
    for k in llm_usage.env_keys("GEMINI") + llm_usage.env_keys("GOOGLE"):
        if k not in keys:
            keys.append(k)
    return keys


def generate_json(api_key: Optional[str], prompt: str, system: Optional[str] = None, temperature: float = 0.2,
                  timeout: float = 90.0, model: Optional[str] = None, max_wait: float = 65.0) -> Any:
    """Tries every configured key and each key's free model chain. When all of them are only resting for a
    per-minute window, waits for the first one to free up (at most max_wait seconds) and uses it again."""
    keys = all_keys(api_key)
    if not keys:
        raise GeminiError("A Gemini API key is required.")
    last: Optional[Exception] = None
    for _ in range(3):
        for key in keys:
            if not llm_usage.available("gemini", key):
                continue
            try:
                return call_once(key, prompt, system, temperature, timeout, model)
            except GeminiRateLimited as e:
                last = e
            except GeminiError as e:
                last = e
                if "API key" in str(e) or "(403)" in str(e) or "(401)" in str(e):
                    llm_usage.cool_down("gemini", key, 3600, why="auth")
                    continue
                raise
        models = [model] if model else sorted({m for k in keys for m in models_for(k)})
        wait = llm_usage.wait_hint("gemini", keys, models)
        if wait is None or wait > max_wait:
            break
        time.sleep(wait + 0.5)
    raise last or GeminiError("Every Gemini key and model is at its free limit for now.")
