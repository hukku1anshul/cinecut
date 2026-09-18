"""
Re-checks resting API keys with one tiny request each.

The app rests a key (or one of its models) when a provider says a limit was hit, and estimates when the limit lifts.
That estimate can be too long: a daily rest guessed from a vague reply, or a limit the provider lifted early. Here each
key and model resting for more than a few minutes gets one tiny request:
  answers        the rest is lifted and the key is used again at once
  still limited  the rest stays, re-read from the provider's own reply
  model gone     (404, 410) the rest stays; the model walk moves on to the key's next model
Key values are never printed or stored: keys are named by their short hash (llm_usage.key_id).
"""
import time
from typing import Any, Callable, Dict, Optional, Tuple

import httpx

from backend.video_engine import gemini_client, llm_client, llm_usage

PING = "Reply with the word OK."


def _keys() -> Dict[str, Tuple[str, str, Any]]:
    """key id -> (provider, key, extra) for every cloud key the app can use."""
    return {llm_usage.key_id(p, k): (p, k, extra) for p, k, extra in llm_client.cloud_candidates(None)}


def _default_model(provider: str, key: str) -> Optional[str]:
    if provider == "gemini":
        return (gemini_client.models_for(key) or [None])[0]
    if provider == "groq":
        return (llm_client.groq_models(key) or [None])[0]
    if provider == "nvidia":
        return llm_client._models.get(llm_usage.key_id("nvidia", key)) or llm_client.NVIDIA_PREFS[0]
    if provider == "openrouter":
        return llm_client.OPENROUTER_PREFS[0]
    if provider == "cloudflare":
        return llm_client.CF_PREFS[0]
    return None


def ping(provider: str, key: str, model: str, extra: Any = None) -> Tuple[int, str, Optional[float]]:
    """(HTTP status, reply text, retry-after seconds) for one tiny request."""
    try:
        if provider == "gemini":
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                           headers={"x-goog-api-key": key}, timeout=40,
                           json={"contents": [{"parts": [{"text": PING}]}], "generationConfig": {"maxOutputTokens": 5}})
        elif provider == "cloudflare":
            r = httpx.post(f"{llm_client.CF_BASE.format(account=extra)}/run/{model}", headers={"Authorization": f"Bearer {key}"},
                           timeout=40, json={"messages": [{"role": "user", "content": PING}], "max_tokens": 5})
        else:
            base = {"groq": llm_client.GROQ_BASE, "nvidia": llm_client.NVIDIA_BASE, "openrouter": llm_client.OPENROUTER_BASE}[provider]
            body: Dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": PING}], "max_tokens": 5, "temperature": 0}
            if provider == "openrouter":
                if not model.endswith(":free"):
                    return 0, "not a free model", None
                body["provider"] = {"max_price": {"prompt": 0, "completion": 0}}
            r = httpx.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=40)
    except (httpx.HTTPError, KeyError) as e:
        return 0, type(e).__name__, None
    try:
        ra = float(r.headers.get("retry-after") or 0) or None
    except ValueError:
        ra = None
    return r.status_code, r.text[:400], ra


def recheck_resting(min_left: float = 300.0, progress: Optional[Callable[[str], None]] = None) -> Dict[str, int]:
    """One tiny request for every key and model that is resting for at least `min_left` more seconds."""
    keys = _keys()
    now = time.time()
    with llm_usage._lock:
        slots = {name: dict(s) for name, s in llm_usage._load()["slots"].items()}
    counts = {"lifted": 0, "still": 0, "gone": 0, "unknown": 0}
    for name, s in slots.items():
        if s.get("blocked", 0.0) - now < min_left:
            continue
        kid, _, model = name.partition("|")
        if kid not in keys:
            continue                              # a key no longer in .env
        provider, key, extra = keys[kid]
        slot_model = None if model == "*" else model
        use = slot_model or _default_model(provider, key)
        if not use:
            continue
        code, text, ra = ping(provider, key, use, extra)
        if code == 200:
            llm_usage.clear(provider, key, slot_model)
            counts["lifted"] += 1
            verdict = "answers: rest lifted"
        elif code == 429 or "RESOURCE_EXHAUSTED" in text:
            llm_usage.note_429(provider, key, slot_model, text, ra)
            counts["still"] += 1
            verdict = "still limited"
        elif code in (404, 410) or "no longer available" in text:
            counts["gone"] += 1
            verdict = "model not available to this key"
        else:
            counts["unknown"] += 1
            verdict = f"HTTP {code}"
        if progress:
            progress(f"{kid} {use}: {verdict}")
    return counts
