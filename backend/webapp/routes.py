"""
HTTP API for the CineCut web app (/api/app/...), the partner API (/api/v1/...) and the studio's library tools
(/api/studio/library/..., this PC only).

The web pages are in frontend/app/. Heavy work (analysis, narration, dubbing, explainers) runs in background threads,
at most CINECUT_WEB_JOBS (2) at a time so the GPU is not overloaded, and is tracked as work items the pages poll.
Everything is free for now (see payments.py); access checks are in place for when plans are charged.
"""
import asyncio
import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import BASE_DIR, OUTPUT_DIR, TEMP_DIR
from backend.video_engine import privacy, rights
from backend.webapp import agreement, db, library, payments

router = APIRouter()
db.init()
COOKIE = "cc_session"
from backend.webapp.jobs import HEAVY  # noqa: E402  (shared with the library builder)
LANGUAGES = ["English", "Hindi", "Bengali", "Tamil", "Telugu", "Marathi", "Gujarati", "Kannada", "Malayalam", "Punjabi"]
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
DOC_EXTS = {".txt", ".md", ".pdf", ".srt", ".vtt"}
UPLOAD_DIR = TEMP_DIR / "web_uploads"
MAX_UPLOAD_MB = int(os.environ.get("CINECUT_MAX_UPLOAD_MB", "4096"))
API_PER_MINUTE = int(os.environ.get("CINECUT_API_PER_MINUTE", "120"))


def _studio():
    import backend.app as appmod          # imported late: app.py includes this router
    return appmod


# ------------------------------------------------------------------ helpers
def current_user(request: Request) -> Optional[Dict[str, Any]]:
    return db.user_for_session(request.cookies.get(COOKIE))


def need_user(request: Request) -> Dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Log in to continue.")
    return user


def _terms_hash() -> str:
    """Fingerprint of the Terms page as it stood when it was accepted."""
    try:
        return hashlib.sha256((BASE_DIR / "frontend" / "terms.html").read_bytes()).hexdigest()
    except OSError:
        return ""


def _client_ip(request: Request) -> str:
    """The address the acceptance came from (behind --lan there is no proxy, so the socket address is the honest one)."""
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))[:60]


def need_role(org_id: int, user: Dict[str, Any], roles=("owner", "admin", "member")) -> Dict[str, Any]:
    if db.role_in(org_id, user["id"]) not in roles:
        raise HTTPException(status_code=403, detail="You are not allowed to do that in this workspace.")
    return db.get_org(org_id)


def need_access(user: Dict[str, Any], stream: str, org_id: Optional[int] = None) -> None:
    if not payments.has_access(user["id"], stream, org_id):
        raise HTTPException(status_code=402, detail="This needs an active plan. Choose one on the Plans page.")


def _can_view(entry: Dict[str, Any], user: Optional[Dict[str, Any]]) -> bool:
    vis = entry.get("visibility", "public")
    if vis == "public":
        return True
    if user and vis.startswith("org:"):
        return db.role_in(int(vis[4:]), user["id"]) is not None
    return bool(user and vis == f"user:{user['id']}")


def viewer_country(request: Request, user: Optional[Dict[str, Any]] = None) -> str:
    """The country whose copyright rules apply to this viewer: the one saved on the account, else the one the CDN reports
    (Cloudflare's CF-IPCountry header), else the market this server serves (CINECUT_MARKET, India by default)."""
    if user:
        saved = db.user_country(user["id"])
        if saved:
            return saved
    c = (request.headers.get("cf-ipcountry") or "").strip().upper()
    c = "GB" if c == "UK" else c
    if len(c) == 2 and c not in ("XX", "T1"):
        return c
    return (os.environ.get("CINECUT_MARKET") or "IN").strip().upper()


def _title_or_404(tid: str, user: Optional[Dict[str, Any]], request: Optional[Request] = None) -> Dict[str, Any]:
    entry = library.get(tid)
    if not entry or not _can_view(entry, user):
        raise HTTPException(status_code=404, detail="Title not found.")
    if request is not None and not library.cleared_in(entry, viewer_country(request, user)):
        raise HTTPException(status_code=451, detail="This title is not cleared for your country, so it cannot be shown here.")
    return entry


def _spawn(wid: str, fn: Callable[[Callable[[float, str], None]], Dict[str, Any]]) -> None:
    def run():
        db.update_work(wid, status="running", message="Waiting for a free worker...")
        try:
            with HEAVY:
                result = fn(lambda p, m: db.update_work(wid, progress=round(float(p), 1), message=str(m)[:300]))
            db.update_work(wid, status="done", progress=100, message="Done.", result=result,
                           minutes=float((result or {}).get("source_minutes") or 0))
        except HTTPException as e:
            db.update_work(wid, status="error", message=str(e.detail)[:400])
        except Exception as e:
            db.update_work(wid, status="error", message=str(e)[:400])
    threading.Thread(target=run, daemon=True, name=f"work-{wid}").start()


def _work_view(w: Dict[str, Any]) -> Dict[str, Any]:
    """A work item without server paths."""
    out = {k: w.get(k) for k in ("id", "kind", "status", "progress", "message", "created", "updated", "org_id")}
    res = w.get("result") or {}
    if isinstance(res, dict):
        out["result"] = {k: v for k, v in res.items() if k not in ("files", "path", "job_id")}
        out["files"] = sorted((res.get("files") or {}).keys())
    params = w.get("params") or {}
    out["params"] = {k: v for k, v in params.items() if k != "path"}
    if w.get("kind") == "shorten":             # watchable until the viewing time runs out
        left = privacy.expires_in(f"wv_{w['id']}")
        out["watchable"] = left is not None and (WEB_PRIVATE / w["id"] / "short.mp4").is_file()
        out["expires_in"] = left
    return out


def _upload(upload_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    w = db.get_work(upload_id)
    if not w or w["kind"] != "upload" or w["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Upload not found. Upload the file again.")
    return w["params"]


# ------------------------------------------------------------------ studio pipeline, run from web work items
def _run_tasks(bt: BackgroundTasks) -> None:
    for t in bt.tasks:
        t.func(*t.args, **t.kwargs)


def studio_job(path: str, minutes: float, mode: str, title: str, basis: Optional[str] = "own", **opts: Any) -> Dict[str, Any]:
    """opts go to the studio's AnalyzeRequest as they are (force_private, preset, filters)."""
    app = _studio()
    app._web_terms.ok = True       # the web user accepted the Terms when signing up
    bt = BackgroundTasks()
    r = app.analyze_video(app.AnalyzeRequest(video_path=path, target_minutes=max(1, int(round(minutes))), content_mode=mode,
                                             rights_basis=basis, film_title=title,
                                             use_vision=bool(os.environ.get("CINECUT_WEB_VISION")), **opts), bt)
    _run_tasks(bt)
    job = app.JOBS[r["job_id"]]
    if job["status"] != "ready":
        raise RuntimeError(job.get("error") or job.get("message") or "Analysis failed.")
    return job


def studio_narrate(job: Dict[str, Any], language: str) -> None:
    app = _studio()
    asyncio.run(app.generate_voiceovers(job["job_id"], app.NarrateRequest(language=language, style="gap")))


def studio_render(job: Dict[str, Any], language: str, voiceover: bool = True) -> str:
    app = _studio()
    bt = BackgroundTasks()
    app.render_job(job["job_id"], app.RenderRequest(include_voiceover=voiceover, language=language), bt)
    _run_tasks(bt)
    if not job.get("output_file"):
        raise RuntimeError(job.get("error") or "Render failed.")
    return job["output_file"]


# ------------------------------------------------------------------ accounts
class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    agree: bool = False


class LoginRequest(BaseModel):
    email: str
    password: str


def _set_cookie(request: Request, response: Response, token: str) -> None:
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"   # behind the tunnel or Render
    response.set_cookie(COOKIE, token, max_age=db.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=https)


@router.post("/api/app/signup")
def signup(req: SignupRequest, request: Request, response: Response):
    if not req.agree:
        raise HTTPException(status_code=400, detail="Accept the Terms of Use to create an account.")
    try:
        user = db.create_user(req.email, req.password, req.name)
    except db.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.record_consent(user["id"], "terms", _studio().TERMS_VERSION, _terms_hash(), ip=_client_ip(request),
                      agent=request.headers.get("user-agent", ""))
    _set_cookie(request, response, db.new_session(user["id"]))
    return {"user": user}


@router.post("/api/app/login")
def login(req: LoginRequest, request: Request, response: Response):
    user = db.verify_login(req.email, req.password)
    if not user:
        time.sleep(0.4)
        raise HTTPException(status_code=401, detail="Wrong email or password.")
    _set_cookie(request, response, db.new_session(user["id"]))
    return {"user": user}


@router.post("/api/app/logout")
def logout(request: Request, response: Response):
    db.end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/api/app/me")
def me(request: Request):
    user = current_user(request)
    base = {"free": payments.free_mode(), "languages": LANGUAGES, "kinds": library.KINDS,
            "country": viewer_country(request, user), "countries": library.COUNTRIES}
    if not user:
        return dict(base, user=None)
    orgs = db.orgs_for_user(user["id"])
    return dict(base, user=user, subscriptions=db.active_subscriptions("user", user["id"]),
                orgs=[dict(o, subscriptions=db.active_subscriptions("org", o["id"])) for o in orgs],
                progress=db.progress_for(user["id"]), balance=db.balance(user["id"]))


class CountryRequest(BaseModel):
    country: str


@router.post("/api/app/me/country")
def set_country(req: CountryRequest, request: Request):
    """Where the viewer lives: the library then shows only titles cleared under that country's copyright rules."""
    user = need_user(request)
    c = req.country.strip().upper()
    if c not in library.COUNTRIES:
        raise HTTPException(status_code=400, detail="Choose India, the United States or the United Kingdom.")
    db.set_user_country(user["id"], c)
    return {"country": c, "country_name": library.COUNTRIES[c]}


# ------------------------------------------------------------------ plans and payments
class CheckoutRequest(BaseModel):
    plan_id: str
    org_id: Optional[int] = None
    seats: int = Field(1, ge=1, le=100000)


class VerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.get("/api/app/plans")
def plans(request: Request):
    country = viewer_country(request, current_user(request))
    cur = payments.currency_for(country)
    return {"plans": payments.public_plans(country), "free": payments.free_mode(), "provider": payments.provider(cur), "currency": cur,
            "tax_note": payments.TAX_NOTE.get(cur, ""), "country": country}


@router.post("/api/app/checkout")
def checkout(req: CheckoutRequest, request: Request):
    user = need_user(request)
    try:
        return payments.checkout(user["id"], req.plan_id, req.org_id, req.seats, viewer_country(request, user),
                                 str(request.base_url).rstrip("/"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/api/app/checkout/verify")
def verify(req: VerifyRequest, request: Request):
    need_user(request)
    order = payments.verify_checkout(req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature)
    if not order:
        raise HTTPException(status_code=400, detail="The payment could not be verified.")
    return {"status": "active", "order_id": order["id"]}


@router.post("/api/app/razorpay/webhook")
async def razorpay_webhook(request: Request):
    ok = payments.handle_webhook(await request.body(), request.headers.get("x-razorpay-signature", ""))
    if not ok:
        raise HTTPException(status_code=400, detail="Bad signature.")
    return {"ok": True}


@router.post("/api/app/stripe/webhook")
async def stripe_webhook(request: Request):
    ok = payments.handle_stripe_webhook(await request.body(), request.headers.get("stripe-signature", ""))
    if not ok:
        raise HTTPException(status_code=400, detail="Bad signature.")
    return {"ok": True}


@router.get("/api/app/orders")
def orders(request: Request):
    return {"orders": db.orders_for(need_user(request)["id"])}


# ------------------------------------------------------------------ library
@router.get("/api/app/library")
def browse(request: Request, kind: Optional[str] = None, q: str = "", language: Optional[str] = None, status: Optional[str] = None,
           offset: int = 0, limit: int = 60):
    country = viewer_country(request, current_user(request))
    page = library.search_page(kind if kind in library.KINDS else None, q, language, status if status in ("ready", "catalog") else None,
                               max(0, offset), max(1, min(200, limit)), country)
    return dict(page, kinds=library.KINDS, stats=library.stats(country), country=country,
                country_name=library.COUNTRIES.get(country, country))


@router.post("/api/app/titles/{tid}/request")
def request_summary(tid: str, request: Request):
    """Asks for a title's summary; asked-for titles are built first."""
    from backend.webapp import builder
    user = need_user(request)
    _title_or_404(tid, user, request)
    try:
        return builder.request(tid)
    except KeyError:
        raise HTTPException(status_code=404, detail="Title not found.")


@router.get("/api/app/titles/{tid}")
def title(tid: str, request: Request, lang: Optional[str] = None, original: bool = False):
    user = current_user(request)
    entry = _title_or_404(tid, user, request)
    country = viewer_country(request, user)
    preferred = library.preferred_language(entry, country)
    out = {"title": library.card(entry), "rights": entry.get("rights"), "script": None, "preferred_language": preferred}
    if entry.get("script"):
        sc = (entry.get("scripts") or {}).get(preferred) or entry["script"]
        out["script"] = {"thesis": sc.get("thesis"), "takeaways": sc.get("takeaways"), "sections": [s["heading"] for s in sc["sections"]],
                         "based_on": sc.get("based_on")}
    if user:
        out["plan"] = library.play_plan(entry, lang or preferred, original)
        out["progress"] = None if original else db.progress_for(user["id"]).get(tid)
    return out


@router.get("/api/app/titles/{tid}/narration/{lang}/{n}")
def narration(tid: str, lang: str, n: int, request: Request):
    user = need_user(request)
    entry = _title_or_404(tid, user, request)
    if entry.get("visibility", "public") == "public":
        need_access(user, "library")
    try:
        path = library.narration_audio(entry, lang, n)
    except IndexError:
        raise HTTPException(status_code=404, detail="No such narration piece.")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return FileResponse(str(path), media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/app/titles/{tid}/media")
def media(tid: str, request: Request):
    """Streams a workspace's own source video to its members (public titles stream from their original site)."""
    user = need_user(request)
    entry = _title_or_404(tid, user, request)
    src = entry.get("source") or {}
    if src.get("backend") != "local" or not os.path.isfile(src.get("path", "")):
        raise HTTPException(status_code=404, detail="This title streams from its original site.")
    return FileResponse(src["path"], media_type="video/mp4")


class ProgressRequest(BaseModel):
    position: float = 0
    mode: str = "video"


@router.post("/api/app/titles/{tid}/progress")
def progress(tid: str, req: ProgressRequest, request: Request):
    user = need_user(request)
    _title_or_404(tid, user, request)
    db.save_progress(user["id"], tid, req.position, req.mode if req.mode in ("video", "audio") else "video")
    return {"ok": True}


class ExplainRequest(BaseModel):
    gutenberg_id: int
    kind: str = "book"
    language: str = "English"
    minutes: float = Field(10, ge=3, le=30)


def _public_explainer(gid: int, kind: str, language: str, minutes: float, progress) -> Dict[str, Any]:
    from backend.video_engine import explainer as ex
    report = rights.check_gutenberg(gid)
    if report["status"] != "cleared":
        raise ValueError(" ".join(report.get("reasons") or ["This book is not public domain in India."]))
    work = TEMP_DIR / f"webex_{uuid.uuid4().hex[:8]}"
    try:
        text = ex.gutenberg_text(report["text_url"])
        src = {"title": report["title"], "author": report.get("creator", ""), "text": text, "attribution": report["attribution"],
               "origin": report["url"], "license_label": report["license"]["label"]}
        res = ex.make_explainer(src, kind, minutes, language if language in ex.WPM else "English", None, work / "w", work / "out",
                                render=False, progress=progress)
        entry = library.from_explainer(res, dict(report, title=report["title"]), kind, "public", report["url"])
        return {"title_id": entry["id"], "title": entry["title"], "words": res.get("words"), "originality_pct": res.get("originality_pct")}
    finally:
        shutil.rmtree(work, ignore_errors=True)       # only the recipe is kept


@router.post("/api/app/explainers")
def request_explainer(req: ExplainRequest, request: Request):
    user = need_user(request)
    need_access(user, "library")
    wid = db.create_work(user["id"], "explainer", req.dict())
    _spawn(wid, lambda p: _public_explainer(req.gutenberg_id, req.kind if req.kind in ("book", "story", "lecture") else "book",
                                            req.language, req.minutes, p))
    return {"work_id": wid}


@router.get("/api/app/work")
def my_work(request: Request, org_id: Optional[int] = None):
    user = need_user(request)
    if org_id:
        need_role(org_id, user, ("owner", "admin"))
    return {"work": [_work_view(w) for w in db.work_for(user["id"], org_id) if w and w["kind"] != "upload"]}


@router.get("/api/app/work/{wid}")
def work_item(wid: str, request: Request):
    user = need_user(request)
    w = db.get_work(wid)
    if not w or (w["user_id"] != user["id"] and not (w.get("org_id") and db.role_in(w["org_id"], user["id"]) in ("owner", "admin"))):
        raise HTTPException(status_code=404, detail="Not found.")
    return _work_view(w)


@router.get("/api/app/work/{wid}/file/{name}")
def work_file(wid: str, name: str, request: Request, download: bool = False):
    user = need_user(request)
    w = db.get_work(wid)
    if not w or w["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Not found.")
    path = ((w.get("result") or {}).get("files") or {}).get(name)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found.")
    media_type = "video/mp4" if path.endswith(".mp4") else "text/plain; charset=utf-8"
    headers = {"Content-Disposition": f'attachment; filename="{Path(path).name}"'} if download else None
    return FileResponse(path, media_type=media_type, headers=headers)


# ------------------------------------------------------------------ uploads (creators and workspaces)
@router.get("/api/app/agreement")
def upload_agreement():
    """The Uploader Agreement, shown in full before a file is sent. The hash comes back with the acceptance."""
    return agreement.summary()


@router.post("/api/app/uploads")
def upload(request: Request, file: UploadFile = File(...), basis: str = Form(""), details: str = Form(""),
           agreed_hash: str = Form(""), org_id: Optional[int] = Form(None)):
    user = need_user(request)
    if basis not in agreement.BASES:
        raise HTTPException(status_code=400, detail="Choose what gives you the right to use this file.")
    if agreed_hash != agreement.text_hash():
        raise HTTPException(status_code=400, detail="The Uploader Agreement has changed. Reload the page, read it and accept it again.")
    if basis != "own" and not details.strip():
        raise HTTPException(status_code=400, detail=f"{agreement.BASIS_DETAIL[basis]}: please fill this in.")
    if org_id:
        need_role(org_id, user, ("owner", "admin"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(file.filename or "upload").name).strip(" .") or "upload"
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTS | DOC_EXTS:
        raise HTTPException(status_code=400, detail="Upload a video (mp4, mkv, mov, webm) or a document (txt, md, pdf, srt).")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f"u{user['id']}_{uuid.uuid4().hex[:8]}{ext}"
    size = 0
    digest = hashlib.sha256()               # the fingerprint that ties the agreement to this exact file
    with open(dest, "wb") as out:
        while True:
            chunk = file.file.read(8 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"Files up to {MAX_UPLOAD_MB} MB can be uploaded.")
            digest.update(chunk)
            out.write(chunk)
    minutes = 0.0
    if ext in VIDEO_EXTS:
        try:
            from backend.video_engine.probe import get_video_metadata
            minutes = round(get_video_metadata(str(dest))["duration_sec"] / 60, 1)
        except Exception:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="That video could not be read.")
    app = _studio()
    rights.RIGHTS_BY_PATH[app._path_key(str(dest))] = {"report": None, "dl_dir": str(dest), "uploaded": True}
    rights.record_declaration(f"web upload by user {user['id']}: {name}", basis, None, app.TERMS_VERSION)
    wid = db.create_work(user["id"], "upload", {"path": str(dest), "name": name, "kind": "video" if ext in VIDEO_EXTS else "document",
                                                "minutes": minutes, "org_id": org_id, "basis": basis, "details": details.strip(),
                                                "sha256": digest.hexdigest()}, org_id)
    db.record_consent(user["id"], "upload", agreement.VERSION, agreement.text_hash(), basis, details.strip(), wid, name,
                      digest.hexdigest(), size, _client_ip(request), request.headers.get("user-agent", ""))
    db.update_work(wid, status="done", progress=100, message="Uploaded.")
    return {"upload_id": wid, "name": name, "kind": "video" if ext in VIDEO_EXTS else "document", "minutes": minutes}


@router.get("/api/app/uploads")
def my_uploads(request: Request):
    """The files the user has sent us, what they declared about each, and whether it is still stored."""
    user = need_user(request)
    out = []
    for w in db.work_for(user["id"], limit=200):
        if w["kind"] != "upload":
            continue
        p = w.get("params") or {}
        out.append({"id": w["id"], "name": p.get("name"), "kind": p.get("kind"), "minutes": p.get("minutes"),
                    "basis": p.get("basis"), "basis_text": agreement.BASES.get(p.get("basis") or "", ""),
                    "details": p.get("details"), "sha256": (p.get("sha256") or "")[:16], "created": w["created"],
                    "deleted": w["status"] == "deleted"})
    return {"uploads": out, "agreement_version": agreement.VERSION}


@router.get("/api/app/uploads/{wid}/agreement")
def upload_agreement_record(wid: str, request: Request, download: bool = False):
    """The user's copy of what they accepted for this file: kept even after the file itself is deleted."""
    user = need_user(request)
    w = db.get_work(wid)
    if not w or w["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Not found.")
    row = db.consent_for_work(wid)
    if not row:
        raise HTTPException(status_code=404, detail="No agreement was recorded for this upload.")
    headers = {"Content-Disposition": f'attachment; filename="cinecut-agreement-{wid}.txt"'} if download else None
    return Response(agreement.receipt(row), media_type="text/plain; charset=utf-8", headers=headers)


@router.delete("/api/app/uploads/{wid}")
def delete_upload(wid: str, request: Request):
    """Deletes the uploaded file and everything made from it, as the agreement promises. The record of what was
    agreed stays, because it is the evidence about a file that once existed."""
    user = need_user(request)
    w = db.get_work(wid)
    if not w or w["user_id"] != user["id"] or w["kind"] != "upload":
        raise HTTPException(status_code=404, detail="Upload not found.")
    removed = 0
    path = (w.get("params") or {}).get("path")
    if path and os.path.isfile(path):
        os.unlink(path)
        removed += 1
    for job in db.work_for(user["id"], limit=200):             # the cuts, reels, dubs and explainers made from it
        if (job.get("params") or {}).get("upload_id") != wid:
            continue
        for f in ((job.get("result") or {}).get("files") or {}).values():
            if f and os.path.isfile(f):
                os.unlink(f)
                removed += 1
        folder = OUTPUT_DIR / "creators" / f"u{user['id']}" / job["id"]
        shutil.rmtree(folder, ignore_errors=True)
        db.update_work(job["id"], status="deleted", message="Deleted at your request.", result="{}")
    db.update_work(wid, status="deleted", message="File deleted at your request.")
    return {"deleted": True, "files_removed": removed}


# ------------------------------------------------------------------ shorten a link: nothing is kept
# The viewer gives a link and chooses the length and focus. The video is fetched only to make the short version and is
# deleted (with everything made on the way) the moment that is ready; the short version is streamed to the viewer alone,
# never offered as a download, and deleted 60 minutes after it was last watched, on Delete, or when CineCut restarts.
WEB_PRIVATE = TEMP_DIR / "web_private"
PRESET_CHOICES = ("story_focused", "action_energy", "musical_romance", "comedy_fun", "emotional_drama", "hero_spotlight",
                  "villain_lore", "balanced_cinema")


class ShortenRequest(BaseModel):
    url: str = ""
    upload_id: str = ""               # or the viewer's own uploaded video (accepted under the Uploader Agreement)
    minutes: int = Field(10, ge=1, le=60)
    style: str = "movie"              # movie | lecture
    preset: str = "story_focused"
    narrate: bool = True
    language: str = "Hindi"
    agreed_hash: str = ""


@router.get("/api/app/shorten/terms")
def shorten_terms():
    presets = _studio().PRESETS
    return {"version": agreement.SHORTEN_VERSION, "hash": agreement.shorten_hash(), "text": agreement.SHORTEN_TEXT,
            "presets": {k: presets[k]["name"] for k in PRESET_CHOICES if k in presets}, "keep_minutes": privacy.TTL_SEC // 60}


@router.post("/api/app/shorten")
def shorten(req: ShortenRequest, request: Request):
    user = need_user(request)
    need_access(user, "creator")
    if req.agreed_hash != agreement.shorten_hash():
        raise HTTPException(status_code=400, detail="Read the shortening terms and accept them first (reload the page if they changed).")
    if req.preset not in PRESET_CHOICES:
        raise HTTPException(status_code=400, detail="Choose what the short version should focus on.")
    url, up, report, decision = req.url.strip(), None, None, None
    if req.upload_id:
        up = dict(_upload(req.upload_id, user), upload_id=req.upload_id)
        if up["kind"] != "video":
            raise HTTPException(status_code=400, detail="Choose a video file.")
    elif url:
        report, decision = _studio()._checked_link(url, None, True)    # refuses protected streaming services; always private
    else:
        raise HTTPException(status_code=400, detail="Paste a link, or choose a video of your own.")
    what = f"your file: {up['name']}" if up else url[:500]
    wid = db.create_work(user["id"], "shorten", {"url": "" if up else url[:500], "name": up["name"] if up else "", "minutes": req.minutes,
                                                 "style": req.style, "preset": req.preset, "narrate": req.narrate, "language": req.language})
    db.record_consent(user["id"], "shorten", agreement.SHORTEN_VERSION, agreement.shorten_hash(), "view", what, wid,
                      ip=_client_ip(request), agent=request.headers.get("user-agent", ""))
    _spawn(wid, lambda p: _shorten_run(req, url, up, wid, report, decision, p))
    return {"work_id": wid}


def _shorten_run(req: ShortenRequest, url: str, up: Optional[Dict[str, Any]], wid: str, report, decision, progress) -> Dict[str, Any]:
    app = _studio()
    lang = req.language if req.language in LANGUAGES else "Hindi"
    hold, job, src_dir = f"dl_{wid}", None, None
    try:
        if up:                                 # the viewer's own file: it goes too, once its short version exists
            from backend.video_engine.probe import get_video_metadata
            meta = dict(get_video_metadata(up["path"]), file_path=up["path"], youtube_title=Path(up["name"]).stem)
        else:
            progress(4, "Fetching the video. The original is deleted as soon as the short version is ready.")
            meta = app._download_with_ytdlp(url, {}, 720)
            src_dir = Path(meta["file_path"]).parent
            app._register_download(meta, report, decision, hold)
        title = (meta.get("youtube_title") or "Video")[:120]
        if " " not in title:                   # a direct file link names the file: "jsc2026m000044_10_Days_in_Orion~medium"
            title = re.sub(r"^[a-z]{2,5}\d[\da-z]{5,}_", "", re.sub(r"~\w+$", "", title)).replace("_", " ").strip() or "Video"
        progress(25, "Finding the parts that match your choices...")
        job = studio_job(meta["file_path"], req.minutes, "lecture" if req.style == "lecture" else "movie", title, None,
                         force_private=True, preset=req.preset)
        if req.narrate:
            progress(55, "Writing the narration...")
            studio_narrate(job, lang)
        progress(75, "Making the short version...")
        made = studio_render(job, lang, req.narrate)
        keep = WEB_PRIVATE / wid
        keep.mkdir(parents=True, exist_ok=True)
        short = keep / "short.mp4"
        shutil.move(made, short)
        privacy.register(f"wv_{wid}", [str(keep)], sweep_caches=False)   # deleted after the viewing time, on Delete, or at restart
        from backend.video_engine.probe import get_video_metadata
        return {"title": title, "short_minutes": round(get_video_metadata(str(short))["duration_sec"] / 60, 1),
                "source_minutes": round((meta.get("duration_sec") or 0) / 60, 1), "keep_minutes": privacy.TTL_SEC // 60}
    finally:                                   # the original and everything made on the way go now, finished or not
        if job:
            privacy.purge(job["job_id"], app.JOBS, reason="short version made")
        privacy.purge(hold, None, reason="short version made")
        if src_dir:
            shutil.rmtree(src_dir, ignore_errors=True)
        if up:
            Path(up["path"]).unlink(missing_ok=True)
            db.update_work(up["upload_id"], status="deleted", message="Deleted once its short version was made.")


def _own_shorten(wid: str, request: Request) -> Dict[str, Any]:
    user = need_user(request)
    w = db.get_work(wid)
    if not w or w["user_id"] != user["id"] or w["kind"] != "shorten":
        raise HTTPException(status_code=404, detail="Not found.")
    return w


@router.get("/api/app/shorten/{wid}/watch")
def shorten_watch(wid: str, request: Request):
    """Streams the short version to its viewer only: shown inline, never offered as a file, never cached."""
    _own_shorten(wid, request)
    short = WEB_PRIVATE / wid / "short.mp4"
    if not short.is_file() or not privacy.is_registered(f"wv_{wid}"):
        raise HTTPException(status_code=410, detail="This short version has been deleted, as promised. Shorten the link again to watch it.")
    privacy.touch(f"wv_{wid}")
    return FileResponse(short, media_type="video/mp4", headers={"Content-Disposition": "inline", "Cache-Control": "no-store, private"})


@router.delete("/api/app/shorten/{wid}")
def shorten_delete(wid: str, request: Request):
    _own_shorten(wid, request)
    privacy.purge(f"wv_{wid}", None, reason="deleted by the viewer")
    shutil.rmtree(WEB_PRIVATE / wid, ignore_errors=True)
    db.update_work(wid, status="deleted", message="Deleted.")
    return {"deleted": True}


# ------------------------------------------------------------------ creator plan
class CreatorJob(BaseModel):
    upload_id: str
    action: str                       # cut | reels | dub | explainer
    minutes: float = Field(10, ge=1, le=60)
    language: str = "Hindi"
    style: str = "movie"              # movie | lecture


def _creator_run(user: Dict[str, Any], up: Dict[str, Any], req: CreatorJob, wid: str, progress) -> Dict[str, Any]:
    from backend.video_engine import dubbing, endcard, explainer as ex
    out_dir = OUTPUT_DIR / "creators" / f"u{user['id']}" / wid
    out_dir.mkdir(parents=True, exist_ok=True)
    title = Path(up["name"]).stem
    lang = req.language if req.language in LANGUAGES else "Hindi"
    progress(3, "Reading your video...")
    job = studio_job(up["path"], req.minutes, "lecture" if req.style == "lecture" else "movie", title, up.get("basis") or "own")
    files: Dict[str, str] = {}
    if req.action == "cut":
        progress(40, "Writing the narration...")
        studio_narrate(job, lang)
        progress(60, "Rendering the cut...")
        src = studio_render(job, lang)
        dst = out_dir / f"{re.sub(r'[^A-Za-z0-9]+', '_', title)[:40]}_cut.mp4"
        shutil.move(src, dst)
        files["video"] = str(dst)
    elif req.action == "reels":
        progress(50, "Finding the best moments for reels...")
        shorts = _studio().generate_shorts(job["job_id"], _studio().ShortsRequest(burn_captions=True, caption_style="karaoke", count=3))
        for i, s in enumerate(shorts.get("viral_shorts") or []):
            dst = out_dir / f"reel_{i + 1}.mp4"
            shutil.copyfile(s["file_path"], dst)
            files[f"reel_{i + 1}"] = str(dst)
    elif req.action == "dub":
        if not job.get("subtitles"):
            raise RuntimeError("No speech was found to dub.")
        res = dubbing.dub_video(job["video_path"], job["subtitles"], lang, str(out_dir / f"{re.sub(r'[^A-Za-z0-9]+', '_', title)[:40]}_{lang}.mp4"),
                                str(out_dir / "work"), credit=endcard.credit_for(job.get("rights"), title), title=title,
                                progress=lambda p, m: progress(30 + p * 0.7, m), words=job.get("words"))
        shutil.rmtree(out_dir / "work", ignore_errors=True)
        files.update({"video": res["file"], "subtitles": res["srt"]})
    elif req.action == "explainer":
        if not job.get("subtitles"):
            raise RuntimeError("No speech was found to explain.")
        src = {"title": title, "author": "", "text": ex.transcript_text(job["subtitles"]), "attribution": "", "origin": "creator upload"}
        res = ex.make_explainer(src, "lecture" if req.style == "lecture" else "story", min(req.minutes, 30), lang, None, out_dir / "work", out_dir,
                                render=True, progress=lambda p, m: progress(30 + p * 0.7, m))
        shutil.rmtree(out_dir / "work", ignore_errors=True)
        files.update({k: v for k, v in res["files"].items() if k in ("video", "script", "srt")})
    else:
        raise ValueError("Unknown action.")
    return {"files": files, "source_minutes": up.get("minutes") or 0, "action": req.action, "language": lang, "title": title}


@router.post("/api/app/creator/jobs")
def creator_job(req: CreatorJob, request: Request):
    user = need_user(request)
    need_access(user, "creator")
    up = _upload(req.upload_id, user)
    if up["kind"] != "video":
        raise HTTPException(status_code=400, detail="Creator tools need a video.")
    if req.action not in ("cut", "reels", "dub", "explainer"):
        raise HTTPException(status_code=400, detail="Choose cut, reels, dub or explainer.")
    if not payments.free_mode():
        plan_minutes = max([p.get("minutes", 0) for s in db.active_subscriptions("user", user["id"])
                            for p in [payments.plan_by_id(s["plan_id"]) or {}]] or [0])
        if db.minutes_used(user["id"], time.time() - 30 * 86400) + (up.get("minutes") or 0) > plan_minutes:
            raise HTTPException(status_code=402, detail="This month's source minutes are used up.")
    wid = db.create_work(user["id"], f"creator_{req.action}", dict(req.dict(), name=up["name"]))
    _spawn(wid, lambda p: _creator_run(user, up, req, wid, p))
    return {"work_id": wid}


# ------------------------------------------------------------------ institutes and companies
class OrgCreate(BaseModel):
    name: str
    kind: str
    languages: str = "English,Hindi"


class JoinRequest(BaseModel):
    code: str


class RoleRequest(BaseModel):
    role: str


class OrgContent(BaseModel):
    upload_id: str
    title: str = ""
    language: str = "English"
    minutes: float = Field(10, ge=2, le=40)
    make: str = "both"                # study | explainer | both (videos); documents always get an explainer
    required: bool = False


class CompleteRequest(BaseModel):
    score: int = 0
    total: int = 0


@router.post("/api/app/orgs")
def create_org(req: OrgCreate, request: Request):
    user = need_user(request)
    try:
        return {"org": db.create_org(user["id"], req.name, req.kind, req.languages)}
    except db.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/app/orgs/join")
def join_org(req: JoinRequest, request: Request):
    user = need_user(request)
    try:
        return {"org": db.join_org(user["id"], req.code)}
    except db.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/app/orgs/{org_id}")
def org_home(org_id: int, request: Request):
    user = need_user(request)
    org = need_role(org_id, user)
    role = db.role_in(org_id, user["id"])
    shelf = db.org_title_ids(org_id)
    required = {s["title_id"]: bool(s["required"]) for s in shelf}
    titles = [dict(c, required=required.get(c["id"], False)) for c in library.search(ids=[s["title_id"] for s in shelf])]
    done = {r["title_id"]: r for r in db.completion_report(org_id) if r["user_id"] == user["id"]}
    out = {"org": org, "role": role, "titles": titles, "my_completions": done, "subscriptions": db.active_subscriptions("org", org_id)}
    if role in ("owner", "admin"):
        out.update({"members": db.org_members(org_id), "invite_code": org["invite_code"],
                    "work": [_work_view(w) for w in db.work_for(user["id"], org_id) if w and w["kind"] != "upload"]})
    else:
        out["org"] = {k: v for k, v in org.items() if k != "invite_code"}
    return out


@router.post("/api/app/orgs/{org_id}/members/{member_id}")
def set_role(org_id: int, member_id: int, req: RoleRequest, request: Request):
    need_role(org_id, need_user(request), ("owner", "admin"))
    try:
        db.set_role(org_id, member_id, req.role)
    except db.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/api/app/orgs/{org_id}/members/{member_id}")
def remove_member(org_id: int, member_id: int, request: Request):
    need_role(org_id, need_user(request), ("owner", "admin"))
    db.remove_member(org_id, member_id)
    return {"ok": True}


def _org_content_run(user: Dict[str, Any], org: Dict[str, Any], up: Dict[str, Any], req: OrgContent, progress) -> Dict[str, Any]:
    from backend.video_engine import explainer as ex
    vis = f"org:{org['id']}"
    title = (req.title or Path(up["name"]).stem).strip()[:120]
    lang = req.language if req.language in LANGUAGES else "English"
    decision = {"status": "declared", "attribution": f"{title}, provided by {org['name']}", "creator": org["name"], "title": title}
    made = []
    work = TEMP_DIR / f"orgex_{uuid.uuid4().hex[:8]}"
    try:
        if up["kind"] == "video":
            progress(3, "Reading the video...")
            job = studio_job(up["path"], req.minutes, "lecture", title, up.get("basis") or "own")
            if req.make in ("study", "both"):
                progress(35, "Writing the study-cut narration...")
                studio_narrate(job, lang)
                job["rights"] = dict(job.get("rights") or {}, attribution=decision["attribution"], creator=org["name"])
                entry = library.from_job(job, kind="lecture", visibility=vis, title=f"{title} (study cut)",
                                         source={"backend": "local", "path": up["path"], "duration": (job.get("metadata") or {}).get("duration_sec")})
                made.append(entry["id"])
            text = ex.transcript_text(job.get("subtitles") or []) if req.make in ("explainer", "both") else ""
        else:
            text = ex.file_text(up["path"])
        if text and len(text.split()) >= 150:
            progress(60, "Writing the own-words explainer...")
            src = {"title": title, "author": org["name"], "text": text, "attribution": decision["attribution"], "origin": org["name"]}
            res = ex.make_explainer(src, "lecture" if org["kind"] == "institute" else "book", req.minutes, lang, None, work / "w", work / "out",
                                    render=False, progress=lambda p, m: progress(60 + p * 0.38, m))
            entry = library.from_explainer(res, decision, "lecture", vis, title=f"{title} (explainer)")
            made.append(entry["id"])
        for tid in made:
            db.add_org_title(org["id"], tid, user["id"], req.required)
        if not made:
            raise RuntimeError("Nothing could be made: the file has too little speech or text (at least 150 words).")
        return {"title_ids": made, "title": title, "source_minutes": up.get("minutes") or 0}
    finally:
        shutil.rmtree(work, ignore_errors=True)


@router.post("/api/app/orgs/{org_id}/content")
def add_org_content(org_id: int, req: OrgContent, request: Request):
    user = need_user(request)
    org = need_role(org_id, user, ("owner", "admin"))
    need_access(user, org["kind"], org_id)
    up = _upload(req.upload_id, user)
    wid = db.create_work(user["id"], "org_content", dict(req.dict(), name=up["name"]), org_id)
    _spawn(wid, lambda p: _org_content_run(user, org, up, req, p))
    return {"work_id": wid}


@router.delete("/api/app/orgs/{org_id}/titles/{tid}")
def remove_org_title(org_id: int, tid: str, request: Request):
    need_role(org_id, need_user(request), ("owner", "admin"))
    db.remove_org_title(org_id, tid)
    entry = library.get(tid)
    if entry and entry.get("visibility") == f"org:{org_id}":
        library.delete(tid)
    return {"ok": True}


@router.post("/api/app/orgs/{org_id}/titles/{tid}/complete")
def complete(org_id: int, tid: str, req: CompleteRequest, request: Request):
    user = need_user(request)
    need_role(org_id, user)
    db.record_completion(org_id, user["id"], tid, req.score, req.total)
    return {"ok": True}


@router.get("/api/app/orgs/{org_id}/report")
def org_report(org_id: int, request: Request):
    need_role(org_id, need_user(request), ("owner", "admin"))
    members = db.org_members(org_id)
    titles = library.search(ids=[s["title_id"] for s in db.org_title_ids(org_id)])
    rows = db.completion_report(org_id)
    return {"members": members, "titles": [{"id": t["id"], "title": t["title"]} for t in titles], "completions": rows,
            "completion_rate": round(100 * len(rows) / max(1, len(members) * len(titles)), 1)}


# ------------------------------------------------------------------ partner API keys
class KeyRequest(BaseModel):
    name: str = "API key"


@router.get("/api/app/api-keys")
def list_keys(request: Request):
    return {"keys": db.api_keys_for(need_user(request)["id"])}


@router.post("/api/app/api-keys")
def new_key(req: KeyRequest, request: Request):
    user = need_user(request)
    need_access(user, "api")
    return db.create_api_key(user["id"], req.name)


@router.delete("/api/app/api-keys/{key_id}")
def revoke_key(key_id: str, request: Request):
    db.revoke_api_key(need_user(request)["id"], key_id)
    return {"ok": True}


@router.get("/api/app/api-usage")
def api_usage(request: Request, days: int = 30):
    return {"usage": db.api_usage_report(need_user(request)["id"], max(1, min(365, days)))}


def api_client(request: Request, endpoint: str, title_id: Optional[str] = None) -> Dict[str, Any]:
    auth = request.headers.get("authorization", "")
    token = request.headers.get("x-api-key") or (auth[7:] if auth.lower().startswith("bearer ") else "")
    key = db.api_key_owner(token.strip())
    if not key:
        raise HTTPException(status_code=401, detail="Send a valid API key in the X-API-Key header.")
    if db.api_calls_since(key["id"], 60) >= API_PER_MINUTE:
        raise HTTPException(status_code=429, detail=f"At most {API_PER_MINUTE} calls a minute.", headers={"Retry-After": "30"})
    db.log_api_usage(key["id"], endpoint, title_id)
    return key


@router.get("/api/v1/catalog")
def v1_catalog(request: Request, kind: Optional[str] = None, language: Optional[str] = None, q: str = "",
               status: Optional[str] = None, offset: int = 0, limit: int = 100, country: str = "IN"):
    """One page of the catalogue (the library holds tens of thousands of titles): page with offset and limit (at most 500)."""
    api_client(request, "catalog")
    offset, limit = max(0, offset), max(1, min(500, limit))
    page = library.search_page(kind if kind in library.KINDS else None, q, language, status if status in ("ready", "catalog") else None,
                               offset, limit, country.strip().upper())
    return {"titles": [dict(t, recipe=f"/api/v1/titles/{t['id']}") for t in page["titles"]], "total": page["total"],
            "country": country.strip().upper(),
            "offset": offset, "limit": limit, "next_offset": offset + limit if offset + limit < page["total"] else None,
            "terms": "Every title keeps its credit line and the made-with-AI label wherever it is shown."}


@router.get("/api/v1/titles/{tid}")
def v1_title(tid: str, request: Request, lang: Optional[str] = None, country: str = "IN"):
    api_client(request, "title", tid)
    entry = library.get(tid)
    if entry and not library.cleared_in(entry, country.strip().upper()):
        raise HTTPException(status_code=451, detail="This title is not cleared for that country.")
    if not entry or entry.get("visibility", "public") != "public":
        raise HTTPException(status_code=404, detail="Title not found.")
    plan = library.play_plan(entry, lang)
    lang = plan["language"]
    plan["narration_text"] = library.narration_texts(entry, lang) if lang else []
    plan["audio"] = [f"/api/v1/titles/{tid}/narration/{lang}/{i}" for i in range(len(plan["narration_text"]))]
    plan["rights"] = entry.get("rights")
    return plan


@router.get("/api/v1/titles/{tid}/narration/{lang}/{n}")
def v1_narration(tid: str, lang: str, n: int, request: Request, country: str = "IN"):
    api_client(request, "narration", tid)
    entry = library.get(tid)
    if entry and not library.cleared_in(entry, country.strip().upper()):
        raise HTTPException(status_code=451, detail="This title is not cleared for that country.")
    if not entry or entry.get("visibility", "public") != "public":
        raise HTTPException(status_code=404, detail="Title not found.")
    try:
        return FileResponse(str(library.narration_audio(entry, lang, n)), media_type="audio/mpeg")
    except IndexError:
        raise HTTPException(status_code=404, detail="No such narration piece.")


@router.post("/api/v1/explainers")
def v1_explainer(req: ExplainRequest, request: Request):
    key = api_client(request, "explainer")
    wid = db.create_work(key["user_id"], "explainer", req.dict())
    _spawn(wid, lambda p: _public_explainer(req.gutenberg_id, req.kind if req.kind in ("book", "story", "lecture") else "book",
                                            req.language, req.minutes, p))
    return {"work_id": wid, "status": f"/api/v1/work/{wid}"}


@router.get("/api/v1/work/{wid}")
def v1_work(wid: str, request: Request):
    key = api_client(request, "work")
    w = db.get_work(wid)
    if not w or w["user_id"] != key["user_id"]:
        raise HTTPException(status_code=404, detail="Not found.")
    return _work_view(w)


# ------------------------------------------------------------------ studio: adding to the library (this PC only)
class AddToLibrary(BaseModel):
    kind: Optional[str] = None
    series: Optional[str] = None
    episode: Optional[int] = None
    title: Optional[str] = None


@router.post("/api/studio/library/job/{job_id}")
def studio_add_job(job_id: str, req: AddToLibrary):
    job = _studio().get_job(job_id)
    if req.kind and req.kind not in library.KINDS:
        raise HTTPException(status_code=400, detail="Unknown kind.")
    if not any((s.get("bridge_narration") or "").strip() for s in job.get("scenes") or [] if s.get("selected", True)):
        raise HTTPException(status_code=400, detail="Generate the narration first; the library keeps its text.")
    try:
        entry = library.from_job(job, req.kind, req.series, req.episode, "public", title=req.title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"title": library.card(entry)}


@router.post("/api/studio/library/explainer/{eid}")
def studio_add_explainer(eid: str, req: AddToLibrary):
    task = _studio().EXPLAINERS.get(eid)
    if not task or task.get("status") != "done":
        raise HTTPException(status_code=404, detail="Explainer not found.")
    res = task["result"]
    decision = dict(res.get("rights") or {})
    try:
        entry = library.from_explainer(res, decision, req.kind or res.get("kind") or "book", "public", decision.get("url", ""), req.title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"title": library.card(entry)}


@router.get("/api/studio/library")
def studio_library():
    return {"stats": library.cache_stats(), "shelves": library.stats()}


class HarvestRequest(BaseModel):
    per_type: Optional[int] = Field(None, ge=10, le=100000)      # None: every title the sources offer
    kinds: Optional[List[str]] = None


@router.post("/api/studio/library/harvest")
def studio_harvest(req: HarvestRequest):
    """Finds public-domain and openly licensed titles until every shelf has per_type of them (runs in the background)."""
    from backend.webapp import catalog
    if catalog.STATE["running"]:
        return catalog.STATE
    threading.Thread(target=lambda: catalog.harvest(req.per_type, req.kinds), daemon=True, name="catalog-harvest").start()
    return {"started": True}


@router.get("/api/studio/library/harvest")
def studio_harvest_status():
    from backend.webapp import catalog
    return catalog.STATE


class BuilderAction(BaseModel):
    action: str = "status"               # start | stop | status


@router.post("/api/studio/library/builder")
def studio_builder(req: BuilderAction):
    from backend.webapp import builder
    if req.action == "start":
        return builder.start()
    if req.action == "stop":
        return builder.stop()
    return builder.status()


@router.get("/api/app/library/builder")
def builder_status():
    """Public progress of the library (how many summaries are ready, what is being made now)."""
    from backend.webapp import builder
    s = builder.status()
    return {k: s[k] for k in ("running", "current", "built", "library", "resting_for_min", "language")}


@router.delete("/api/studio/library/{tid}")
def studio_delete(tid: str):
    return {"deleted": library.delete(tid)}
