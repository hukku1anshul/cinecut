"""
One entry point for the language model used by narration, lecture notes and story analysis.

Order (free tiers only, each key limited by a daily cap in llm_usage and rested when rate limited):
  1. Gemini: the key typed in the app, then GEMINI_API_KEY, GEMINI_API_KEY_2, ... (Flash models)
  2. Groq free tier            (GROQ_API_KEY[_n])
  3. Cloudflare Workers AI     (CLOUDFLARE_API_KEY[_n] + CLOUDFLARE_ACCOUNT_ID[_n], daily free allocation)
  4. NVIDIA API catalog        (NVIDIA_API_KEY[_n], free trial credits)
  5. Local Ollama model        (unlimited, offline)
If a provider fails or hits its limit, the next one is tried automatically.
"""
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.video_engine import gemini_client, llm_usage

OLLAMA_URL = os.environ.get("CINECUT_OLLAMA_URL", "http://127.0.0.1:11434")
GROQ_BASE = "https://api.groq.com/openai/v1"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
CF_BASE = "https://api.cloudflare.com/client/v4/accounts/{account}/ai"

PREFERRED_MODELS = ("qwen2.5", "qwen3", "gemma3", "gemma2", "llama3.1", "llama3.2", "aya", "mistral")
TRANSLATION_MODELS = ("aya-expanse", "aya", "gemma3", "qwen3")
GROQ_PREFS = ("qwen/qwen3.8-27b", "openai/gpt-oss-120b", "qwen/qwen3.6-27b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b")
CF_PREFS = ("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/qwen/qwen3.8-27b", "@cf/openai/gpt-oss-120b",
            "@cf/google/gemma-4-26b-a4b-it")
NVIDIA_PREFS = ("nvidia/nemotron-3-super-120b-a12b", "google/gemma-4-31b-it", "deepseek-ai/deepseek-v4-flash-0731",
                "mistralai/mistral-large", "mistralai/mistral-nemotron", "google/gemma-3-12b-it")   # retired models (410) are skipped
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_PREFS = ("nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "google/gemma-4-26b-a4b-it:free")   # free models only: the account holds credit, and a paid model would spend it
LABELS = {"gemini": "Gemini free tier", "groq": "Groq free tier", "cloudflare": "Cloudflare Workers AI free allocation",
          "nvidia": "NVIDIA free credits", "openrouter": "OpenRouter free models"}
_EMBEDDING_HINTS = ("embed", "bge", "minilm", "rerank")

_cache: Dict[str, Any] = {"checked": 0.0, "model": None}
_models: Dict[str, Optional[str]] = {}
_ollama_lock = threading.Lock()
last_used: Dict[str, str] = {}


class LLMUnavailable(RuntimeError):
    pass


class RateLimited(RuntimeError):
    def __init__(self, message: str, retry_after: float = 60.0):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderError(RuntimeError):
    def __init__(self, message: str, auth: bool = False):
        super().__init__(message)
        self.auth = auth


# ------------------------------------------------------------------ local Ollama
def ollama_model() -> Optional[str]:
    override = os.environ.get("CINECUT_OLLAMA_MODEL")
    now = time.time()
    if now - _cache["checked"] < 60:
        return override or _cache["model"]
    model = None
    try:
        res = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        names = [m["name"] for m in res.json().get("models", []) if not any(h in m["name"].lower() for h in _EMBEDDING_HINTS)]
        for pref in PREFERRED_MODELS:
            model = next((n for n in names if n.lower().startswith(pref)), None)
            if model:
                break
        if not model and names:
            model = names[0]
    except Exception:
        model = None
    _cache.update(checked=now, model=model)
    return override or model


def translation_model() -> Optional[str]:
    try:
        names = [m["name"] for m in httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5).json().get("models", [])]
    except Exception:
        return ollama_model()
    for pref in TRANSLATION_MODELS:
        hit = next((n for n in names if n.lower().startswith(pref)), None)
        if hit:
            return hit
    return ollama_model()


# ------------------------------------------------------------------ helpers
def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    return re.sub(r"\s*```$", "", text)


def _parse_json_loose(text: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    text = re.sub(r"(?s)<think>.*?</think>", "", str(text or ""))
    text = _strip_fences(text)
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


def _retry_after(res: httpx.Response, default: float = 60.0) -> float:
    try:
        return float(res.headers.get("retry-after", default))
    except ValueError:
        return default


def _pick(provider: str, key: str, fetch, prefs) -> Optional[str]:
    kid = llm_usage.key_id(provider, key)
    if kid in _models:
        return _models[kid]
    try:
        ids = fetch()
    except Exception:
        ids = []
    model = next((p for p in prefs if p in ids), None) if ids else prefs[0]
    _models[kid] = model
    return model


def _openai_chat(base: str, key: str, model: str, messages, temperature: float, timeout: float, json_mode: bool,
                 extra: Optional[Dict[str, Any]] = None) -> Any:
    body: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": 1024, **(extra or {})}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    res = httpx.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
    if res.status_code == 429:
        raise RateLimited(f"HTTP 429 from {base} ({model}): {res.text[:400]}", _retry_after(res))
    if res.status_code == 400 and json_mode and "response_format" in res.text:
        return _openai_chat(base, key, model, messages, temperature, timeout, False, extra)
    if res.status_code != 200:
        raise ProviderError(f"HTTP {res.status_code}: {res.text[:160]}", auth=res.status_code in (401, 403))
    return _parse_json_loose(res.json()["choices"][0]["message"].get("content") or "")


_groq_models: Dict[str, List[str]] = {}


def groq_models(key: str) -> List[str]:
    kid = llm_usage.key_id("groq", key)
    if kid not in _groq_models:
        try:
            ids = {m["id"] for m in httpx.get(f"{GROQ_BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15).json()["data"]}
        except Exception:
            ids = set()
        _groq_models[kid] = [p for p in GROQ_PREFS if p in ids] or list(GROQ_PREFS[:2])
    return _groq_models[kid]


def _groq(key: str, messages, temperature: float, timeout: float) -> Any:
    """Groq limits each model separately, so a model that is resting is skipped and the key's next model is used."""
    last: Optional[Exception] = None
    models = groq_models(key)
    for model in models:
        if not llm_usage.available("groq", key, model):
            continue
        try:
            out = _openai_chat(GROQ_BASE, key, model, messages, temperature, timeout, True)
        except RateLimited as e:
            llm_usage.note_429("groq", key, model, str(e), e.retry_after)
            last = e
            continue
        llm_usage.record("groq", key, model=model)
        return out
    raise RateLimited(f"Every Groq model on this key is resting ({str(last)[:120] if last else 'limits reached'})",
                      llm_usage.wait_hint("groq", [key], models) or 60.0)


def _nvidia(key: str, messages, temperature: float, timeout: float) -> Any:
    """Some listed NVIDIA models are not enabled for every account (HTTP 404), so try the next one."""
    auth = {"Authorization": f"Bearer {key}"}
    kid = llm_usage.key_id("nvidia", key)
    if _models.get(kid):
        order = [_models[kid]]
    else:
        try:
            ids = [m["id"] for m in httpx.get(f"{NVIDIA_BASE}/models", headers=auth, timeout=15).json()["data"]]
        except Exception:
            ids = list(NVIDIA_PREFS)
        order = [p for p in NVIDIA_PREFS if p in ids] or list(NVIDIA_PREFS)
    last: Optional[Exception] = None
    for model in order:
        try:
            out = _openai_chat(NVIDIA_BASE, key, model, messages, temperature, timeout, False)
            _models[kid] = model
            return out
        except ProviderError as e:
            last = e
            if "404" in str(e) or "410" in str(e):     # not enabled for this account, or retired
                continue
            raise
    raise last or ProviderError("No NVIDIA model answered.")


def _openrouter(key: str, messages, temperature: float, timeout: float) -> Any:
    """OpenRouter's free models only (ids ending in ":free", and a price cap of zero), so an account that holds credit
    never spends it. The free allowance is per account per day, so a limit reply rests the whole key until midnight UTC."""
    last: Optional[Exception] = None
    for model in OPENROUTER_PREFS:
        if not model.endswith(":free"):
            continue
        try:
            return _openai_chat(OPENROUTER_BASE, key, model, messages, temperature, timeout, True,
                                {"provider": {"max_price": {"prompt": 0, "completion": 0}}})
        except ProviderError as e:
            last = e
            if "404" in str(e) or "410" in str(e):
                continue
            raise
    raise last or ProviderError("No free OpenRouter model answered.")


def _cloudflare(key: str, account: str, messages, temperature: float, timeout: float) -> Any:
    auth = {"Authorization": f"Bearer {key}"}
    base = CF_BASE.format(account=account)
    model = _pick("cloudflare", key, lambda: [m["name"] for m in httpx.get(f"{base}/models/search", params={"task": "Text Generation", "per_page": 100}, headers=auth, timeout=15).json()["result"]], CF_PREFS)
    if not model:
        raise ProviderError("No suitable Cloudflare model.")
    res = httpx.post(f"{base}/run/{model}", headers=auth, json={"messages": messages, "temperature": temperature, "max_tokens": 1024}, timeout=timeout)
    if res.status_code == 429 or "daily free allocation" in res.text[:400].lower():
        raise RateLimited("Cloudflare free allocation used", 6 * 3600 if "daily" in res.text.lower() else _retry_after(res))
    if res.status_code != 200:
        raise ProviderError(f"HTTP {res.status_code}: {res.text[:160]}", auth=res.status_code in (401, 403))
    result = res.json().get("result", {})
    content = result.get("response")
    if content is None and result.get("choices"):
        content = result["choices"][0]["message"].get("content")
    return _parse_json_loose(content)


# ------------------------------------------------------------------ routing
def cloud_candidates(api_key: Optional[str] = None) -> List[Tuple[str, str, Optional[str]]]:
    out: List[Tuple[str, str, Optional[str]]] = [("gemini", k, None) for k in gemini_client.all_keys(api_key)]
    out += [("groq", k, None) for k in llm_usage.env_keys("GROQ")]
    if os.environ.get("CINECUT_CLOUDFLARE_TEXT", "0") == "1":   # Cloudflare's free allocation is kept for scene pictures
        out += [("cloudflare", k, acct) for k, acct in llm_usage.cloudflare_accounts()]
    out += [("nvidia", k, None) for k in llm_usage.env_keys("NVIDIA")]
    out += [("openrouter", k, None) for k in llm_usage.env_keys("OPENROUTER")]
    return out


def provider_for(api_key: Optional[str] = None) -> Optional[str]:
    for provider, key, _ in cloud_candidates(api_key):
        if llm_usage.available(provider, key):
            return provider
    return "ollama" if ollama_model() else None


def describe(api_key: Optional[str] = None) -> str:
    first = provider_for(api_key)
    if first is None:
        return "no AI model"
    if first == "ollama":
        return f"local {ollama_model()} (Ollama)"
    others = sorted({p for p, _, _ in cloud_candidates(api_key)} - {first})
    extra = f", with {', '.join(others)}" if others else ""
    local = " and local Ollama" if ollama_model() else ""
    return f"{LABELS[first]}{extra}{local} as fallback" if (others or local) else LABELS[first]


def generate_json(prompt: str, system: Optional[str] = None, api_key: Optional[str] = None,
                  temperature: float = 0.2, timeout: float = 180.0, num_ctx: int = 8192,
                  ollama_model_name: Optional[str] = None, allow_cloud: bool = True) -> Any:
    """Parsed JSON from the first free provider that answers (see module docstring for the order)."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    errors: List[str] = []
    for _round in range(2 if allow_cloud else 0):
        for provider, key, extra in cloud_candidates(api_key):
            if not llm_usage.available(provider, key):
                continue
            try:
                if provider == "gemini":        # call_once walks the key's models and counts per model itself
                    out = gemini_client.call_once(key, prompt, system, temperature, min(timeout, 120.0))
                elif provider == "groq":
                    out = _groq(key, messages, temperature, timeout)
                elif provider == "cloudflare":
                    out = _cloudflare(key, extra, messages, temperature, timeout)
                elif provider == "openrouter":
                    out = _openrouter(key, messages, temperature, timeout)
                else:
                    out = _nvidia(key, messages, temperature, timeout)
                if provider not in ("gemini", "groq"):     # Gemini and Groq count per model themselves
                    llm_usage.record(provider, key)
                last_used["provider"] = provider
                return out
            except gemini_client.GeminiRateLimited:
                errors.append("gemini: every model on this key is resting")
            except RateLimited as e:
                if provider != "groq":                     # Groq rests only the model that hit its limit
                    llm_usage.note_429(provider, key, None, str(e), getattr(e, "retry_after", 60.0))
                errors.append(f"{provider}: rate limited")
            except Exception as e:
                is_auth = getattr(e, "auth", False) or "(401)" in str(e) or "(403)" in str(e) or "API key" in str(e)
                llm_usage.cool_down(provider, key, 3600 if is_auth else 20, why="auth" if is_auth else "error")
                errors.append(f"{provider}: {str(e)[:100]}")
        # Every cloud key is resting. If one frees up within about a minute, reuse it rather than fall back to a slow local model.
        waits = [(llm_usage.wait_hint("gemini", [k], gemini_client.models_for(k)) or 0.0) if p == "gemini" else
                 (llm_usage.wait_hint("groq", [k], groq_models(k)) or 0.0) if p == "groq" else llm_usage.free_in(p, k)
                 for p, k, _ in cloud_candidates(api_key)]
        soonest = min(waits) if waits else None
        if _round == 0 and soonest is not None and 0 < soonest <= 65:
            time.sleep(soonest + 0.5)
            continue
        break

    model = ollama_model_name or ollama_model()
    if model:
        payload = {"model": model, "messages": messages, "format": "json", "stream": False,
                   "keep_alive": "10m", "options": {"temperature": temperature, "num_ctx": num_ctx}}
        with _ollama_lock:
            res = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        if res.status_code != 200:
            raise LLMUnavailable(f"Ollama error {res.status_code}: {res.text[:200]}")
        last_used["provider"] = "ollama"
        return _parse_json_loose(res.json()["message"]["content"])
    raise LLMUnavailable("; ".join(errors[-3:]) or "No AI model available. Add a key to .env or install Ollama.")
