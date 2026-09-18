"""
Scene pictures for the lessons and stories: one picture for each moment of the narration, in a stylised 3D-render film
look, made by Cloudflare Workers AI (FLUX.1 schnell) within its daily free allocation. Cloudflare is kept for pictures
(the text models do not use it unless CINECUT_CLOUDFLARE_TEXT=1). Every picture is kept by its exact prompt, so a
re-render never pays twice; when both accounts' allocations are used up, SceneQuotaUsed is raised and the job waits.

Rules (the viewer's choices): people, places, fire and nature only; the Vedic gods are never drawn by the AI (they stay
as public-domain paintings), so a prompt that names a god is refused here. Hand-picked shots from Adobe Firefly can be
dropped into output/youtube/_scenes/firefly/<scene id>.(mp4|png|jpg) and are used instead of the generated picture
(a clip, e.g. from Firefly's Image to Video, plays as real motion).
"""
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from backend.config import OUTPUT_DIR

SCENES = OUTPUT_DIR / "youtube" / "_scenes"
FIREFLY = SCENES / "firefly"
MODEL = "@cf/black-forest-labs/flux-1-schnell"
STYLE = ("Cinematic 3D rendered animated film still, stylised like a modern video game cutscene, Unreal Engine render, "
         "volumetric light, rich natural colour, expressive detailed faces, wide cinematic composition with the subject in "
         "the middle band. ")
# the viewer: the gods are not drawn by AI
GODS = re.compile(r"\b(agni|indra|varuna|soma|ushas|surya|vishnu|shiva|rudra|brahma|saraswati|lakshmi|ganesh|krishna|rama|"
                  r"hanuman|durga|kali|devi|deity|deities|god|gods|goddess)\b", re.I)


class SceneQuotaUsed(RuntimeError):
    pass


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    p = Path(__file__).resolve().parents[2] / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def _accounts() -> List[Tuple[str, str]]:
    env, out = _env(), []
    for suffix in [""] + [f"_{n}" for n in range(2, 10)]:
        acct, key = env.get(f"CLOUDFLARE_ACCOUNT_ID{suffix}"), env.get(f"CLOUDFLARE_API_KEY{suffix}")
        if acct and key:
            out.append((acct, key))
    return out


_resting: Dict[str, float] = {}          # account id -> time its allocation is back (midnight UTC)


def _next_midnight_utc() -> float:
    now = time.time()
    return now - now % 86400 + 86400 + 120


def key_of(prompt: str, seed: int) -> str:
    return hashlib.sha1(json.dumps([STYLE, prompt, seed, MODEL]).encode("utf-8")).hexdigest()[:20]


def picture(scene_id: str, prompt: str, seed: int = 7, also: Optional[List[str]] = None) -> Path:
    """The picture (or Firefly clip) for one scene, made or taken from the cache: the viewer's own file for this id, then
    for the ids in `also` (a clip falls back to its scene's picture), then a generated picture."""
    if GODS.search(prompt):
        raise ValueError(f"scene {scene_id}: AI scenes never show the gods ({GODS.search(prompt).group(0)})")
    for sid in [scene_id] + list(also or []):
        for ext in ("mp4", "mov", "webm", "m4v", "png", "jpg", "jpeg", "webp"):     # a real clip first, then a still
            own = FIREFLY / f"{sid}.{ext}"
            if own.exists():
                return own
    SCENES.mkdir(parents=True, exist_ok=True)
    out = SCENES / f"{key_of(prompt, seed)}.jpg"
    if out.exists() and out.stat().st_size > 10_000:
        return out
    errors = []
    for acct, key in _accounts():
        if _resting.get(acct, 0) > time.time():
            continue
        try:
            r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{MODEL}",
                           headers={"Authorization": f"Bearer {key}"}, json={"prompt": (STYLE + prompt)[:2000], "steps": 8},
                           timeout=120)
        except httpx.HTTPError as ex:
            errors.append(str(ex)[:80])
            continue
        if r.status_code == 200 and (r.json().get("result") or {}).get("image"):
            out.write_bytes(base64.b64decode(r.json()["result"]["image"]))
            (SCENES / f"{out.stem}.json").write_text(json.dumps({"scene": scene_id, "prompt": prompt, "seed": seed, "model": MODEL,
                                                                "made": time.time()}, ensure_ascii=False), encoding="utf-8")
            return out
        if r.status_code == 429 or "allocation" in r.text:
            _resting[acct] = _next_midnight_utc()
        errors.append(f"{r.status_code}: {r.text[:100]}")
    if not any(_resting.get(a, 0) <= time.time() for a, _ in _accounts()):
        raise SceneQuotaUsed("no scene picture allowance left today (Cloudflare resets at midnight UTC)")
    raise RuntimeError("scene picture failed: " + "; ".join(errors[-2:]))
