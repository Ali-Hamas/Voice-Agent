"""FastAPI app: public signup/login + per-owner admin + Twilio voice routes."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    current_user,
    hash_password,
    login_user,
    logout_user,
    require_login,
    verify_password,
)
from .config import LOG_LEVEL, PORT, PUBLIC_HOST, SECRET_KEY
from .db import (
    count_calls_since,
    create_restaurant,
    create_user,
    get_restaurant,
    get_restaurant_by_number,
    get_restaurant_by_slug,
    get_user_by_email,
    init_db,
    list_orders,
    list_recent_calls,
    list_reservations,
    restaurant_dir,
    update_restaurant,
)
from .forwarding_codes import all_for as forwarding_all_for
from .outbound_caller import dispatcher_loop
from .outbound_scheduler import scheduler_loop
from .rag import fetch_url_text, ingest_for, retrieve, format_context
from .twilio_bridge import run_bridge
from .twilio_provision import activate_number, send_forward_sms

logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voice-agent")

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "app" / "templates"
STATIC_DIR = ROOT / "app" / "static"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Restaurant Voice Agent")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return ""
    from datetime import datetime as _dt
    return _dt.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")


templates.env.filters["datetime"] = _fmt_ts


# inject the logged-in user into every template render
@app.middleware("http")
async def _inject_user(request: Request, call_next):
    response = await call_next(request)
    return response


def _ctx(request: Request, **extra) -> dict:
    return {
        "request": request,
        "user": current_user(request),
        "public_host": PUBLIC_HOST,
        **extra,
    }


_background_stop = asyncio.Event()
_background_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def _startup() -> None:
    init_db()
    log.info("DB ready. PUBLIC_HOST=%s", PUBLIC_HOST or "(not set)")
    _background_stop.clear()
    _background_tasks.append(asyncio.create_task(dispatcher_loop(_background_stop)))
    _background_tasks.append(asyncio.create_task(scheduler_loop(_background_stop)))


@app.on_event("shutdown")
async def _shutdown() -> None:
    _background_stop.set()
    for t in _background_tasks:
        t.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()


# ---------- public pages ----------

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    if current_user(request):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse("landing.html", _ctx(request))


@app.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request) -> HTMLResponse:
    if current_user(request):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse("signup.html", _ctx(request, error=None))


@app.post("/signup")
async def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    restaurant_name: str = Form(...),
    cuisine: str = Form(""),
):
    email = email.lower().strip()
    if not email or "@" not in email:
        return templates.TemplateResponse("signup.html", _ctx(request, error="Enter a valid email."), status_code=400)
    if len(password) < 8:
        return templates.TemplateResponse("signup.html", _ctx(request, error="Password must be at least 8 characters."), status_code=400)
    if get_user_by_email(email):
        return templates.TemplateResponse("signup.html", _ctx(request, error="An account with that email already exists."), status_code=400)
    if not restaurant_name.strip():
        return templates.TemplateResponse("signup.html", _ctx(request, error="Enter a restaurant name."), status_code=400)

    slug = _unique_slug(restaurant_name)
    rid = create_restaurant(
        slug=slug,
        name=restaurant_name.strip(),
        cuisine=cuisine.strip(),
        greeting=f"Hello, thank you for calling {restaurant_name.strip()}. How can I help you today?",
        tone="warm, professional, concise",
        languages="English by default; switch to the caller's language if they use one.",
    )
    uid = create_user(email, hash_password(password), rid)
    login_user(request, uid)
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    if current_user(request):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse("login.html", _ctx(request, error=None))


@app.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse("login.html", _ctx(request, error="Wrong email or password."), status_code=400)
    login_user(request, user["id"])
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/logout")
@app.post("/logout")
async def logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/", status_code=303)


# ---------- health + voice routes ----------

@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/favicon.ico")
async def favicon() -> Response:
    # Minimal 1x1 transparent PNG so browsers stop logging 404.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8"
        b"\xcf\xc0\x00\x00\x00\x03\x00\x01t\x9d\xfd\xe7\x00\x00\x00\x00IEND"
        b"\xaeB`\x82"
    )
    return Response(content=png, media_type="image/png")


@app.post("/voice/incoming")
async def voice_incoming(request: Request) -> Response:
    host = PUBLIC_HOST or request.url.hostname or ""
    if not host:
        return Response("PUBLIC_HOST not configured", status_code=500)
    form = await request.form()
    to_number = (form.get("To") or "").strip()
    from_number = (form.get("From") or "").strip()
    restaurant = get_restaurant_by_number(to_number)
    if not restaurant:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say>This number is not configured for the AI receptionist yet. Goodbye.</Say></Response>"""
        return Response(content=twiml, media_type="application/xml")
    from urllib.parse import quote
    qs = f"?from={quote(from_number)}" if from_number else ""
    ws_url = f"wss://{host}/voice/stream/{restaurant['slug']}{qs}"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/voice/stream/{slug}")
async def voice_stream(ws: WebSocket, slug: str) -> None:
    await ws.accept()
    restaurant = get_restaurant_by_slug(slug)
    if not restaurant:
        log.warning("No restaurant for slug=%s", slug)
        await ws.close()
        return
    raw_job_id = ws.query_params.get("job_id")
    job_id: int | None = None
    if raw_job_id:
        try:
            job_id = int(raw_job_id)
        except ValueError:
            log.warning("Invalid job_id=%r on WS stream", raw_job_id)
    from_number = ws.query_params.get("from") or None
    try:
        await run_bridge(ws, restaurant=restaurant, job_id=job_id, from_number=from_number)
    except Exception:
        log.exception("Bridge crashed")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@app.post("/voice/outbound/status/{job_id}")
async def voice_outbound_status(job_id: int, request: Request) -> Response:
    """Twilio call status callback. Drives retry + outcome state."""
    import time as _time

    from .config import OUTBOUND_MAX_ATTEMPTS, OUTBOUND_RETRY_DELAY_SEC
    from .outbound_jobs import enqueue_job, get_job, mark_done, mark_failed, mark_in_call

    form = await request.form()
    status = (form.get("CallStatus") or "").strip().lower()
    answered_by = (form.get("AnsweredBy") or "").strip().lower()
    log.info("OUTBOUND status: job=%s status=%s answered_by=%s", job_id, status, answered_by)

    job = get_job(job_id)
    if not job:
        return Response(status_code=204)

    def _retry_if_allowed(outcome: str, notes: str = "") -> None:
        attempts = int(job.get("attempts") or 0)
        if attempts >= OUTBOUND_MAX_ATTEMPTS:
            mark_done(job_id, outcome, notes)
            return
        mark_done(job_id, outcome, notes)
        enqueue_job(
            restaurant_id=job["restaurant_id"],
            job_type=job["job_type"],
            to_number=job["to_number"],
            source=f"retry:{job['source']}",
            scheduled_at=int(_time.time()) + OUTBOUND_RETRY_DELAY_SEC,
            reservation_id=job.get("reservation_id"),
            guest_name=job.get("guest_name") or "",
            context=job.get("context") or {},
            attempts=attempts,
        )

    if status == "in-progress":
        if answered_by.startswith("machine") or answered_by == "fax":
            _retry_if_allowed("machine", f"answered_by={answered_by}")
        else:
            mark_in_call(job_id)
        return Response(status_code=204)

    if status == "completed":
        if not job.get("outcome"):
            mark_done(job_id, "completed", "")
        else:
            mark_done(job_id, job["outcome"], job.get("outcome_notes") or "")
        return Response(status_code=204)

    if status in {"no-answer", "busy", "failed"}:
        _retry_if_allowed("no_answer", f"call_status={status}")
        return Response(status_code=204)

    if status == "canceled":
        mark_failed(job_id, "cancelled", "operator cancelled")
        return Response(status_code=204)

    return Response(status_code=204)


@app.api_route("/voice/outbound/{job_id}", methods=["GET", "POST"])
async def voice_outbound(job_id: int, request: Request) -> Response:
    """TwiML returned to Twilio when an outbound call is answered.

    Twilio fetches this URL via `client.calls.create(url=...)`; we stream the
    audio to the same WS endpoint the inbound path uses, with ?job_id= so the
    bridge picks the outbound prompt + tools.
    """
    from .outbound_jobs import get_job

    host = PUBLIC_HOST or request.url.hostname or ""
    job = get_job(job_id)
    if not host or not job:
        twiml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Response><Say>Configuration error. Goodbye.</Say></Response>"
        )
        return Response(content=twiml, media_type="application/xml")
    restaurant = get_restaurant(job["restaurant_id"])
    if not restaurant:
        twiml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Response><Say>Restaurant not found. Goodbye.</Say></Response>"
        )
        return Response(content=twiml, media_type="application/xml")
    ws_url = f"wss://{host}/voice/stream/{restaurant['slug']}?job_id={job_id}"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# ---------- admin (owner) ----------

def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "restaurant"


def _unique_slug(name: str) -> str:
    base = _slugify(name)
    slug = base
    i = 2
    while get_restaurant_by_slug(slug):
        slug = f"{base}-{i}"
        i += 1
    return slug


def _owned(user: dict, rid: int) -> dict:
    """Fetch a restaurant and verify the logged-in user owns it."""
    if user["restaurant_id"] != rid:
        raise HTTPException(status_code=404, detail="Not found")
    r = get_restaurant(rid)
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    return r


@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    r = get_restaurant(user["restaurant_id"])
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant missing")
    return RedirectResponse(url=f"/admin/r/{r['id']}", status_code=303)


@app.get("/admin/r/{rid}", response_class=HTMLResponse)
async def admin_restaurant(request: Request, rid: int, user: dict = Depends(require_login)) -> HTMLResponse:
    from .outbound_jobs import list_jobs_for_restaurant

    r = _owned(user, rid)
    kdir = restaurant_dir(r["slug"]) / "knowledge"
    files = sorted([p.name for p in kdir.iterdir() if p.is_file()])
    forwarding = forwarding_all_for(r.get("twilio_number") or "") if r.get("twilio_number") else []
    return templates.TemplateResponse(
        "restaurant.html",
        _ctx(
            request,
            r=r,
            files=files,
            forwarding=forwarding,
            reservations=list_reservations(rid),
            orders=list_orders(rid),
            jobs=list_jobs_for_restaurant(rid, limit=100),
        ),
    )


@app.get("/admin/r/{rid}/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, rid: int, user: dict = Depends(require_login)) -> HTMLResponse:
    import time as _time
    from datetime import datetime as _dt
    from .outbound_jobs import list_jobs_for_restaurant

    r = _owned(user, rid)
    kdir = restaurant_dir(r["slug"]) / "knowledge"
    knowledge_count = sum(1 for p in kdir.iterdir() if p.is_file())

    today_start = int(_dt.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    week_start = today_start - 6 * 86400

    inbound_today = count_calls_since(rid, today_start)
    inbound_week = count_calls_since(rid, week_start)
    recent_calls = list_recent_calls(rid, limit=10)

    all_jobs = list_jobs_for_restaurant(rid, limit=200)
    queued = sum(1 for j in all_jobs if j["status"] == "queued")
    in_flight = sum(1 for j in all_jobs if j["status"] in ("dialing", "in_call"))
    outbound_today = sum(1 for j in all_jobs if (j.get("created_at") or 0) >= today_start)
    recent_jobs = all_jobs[:10]

    reservations = list_reservations(rid)
    orders = list_orders(rid)
    reservations_today = sum(1 for x in reservations if (x.get("created_at") or 0) >= today_start)
    orders_today = sum(1 for x in orders if (x.get("created_at") or 0) >= today_start)

    health = {
        "inbound_ready": bool(r.get("active") and r.get("twilio_number")),
        "outbound_enabled": bool(r.get("reminder_enabled")),
        "twilio_configured": bool(r.get("twilio_account_sid") and r.get("twilio_auth_token") and r.get("twilio_number")),
        "knowledge_count": knowledge_count,
        "transfer_set": bool(r.get("transfer_number")),
    }

    return templates.TemplateResponse(
        "dashboard.html",
        _ctx(
            request,
            r=r,
            now_ts=int(_time.time()),
            health=health,
            inbound_today=inbound_today,
            inbound_week=inbound_week,
            recent_calls=recent_calls,
            queued=queued,
            in_flight=in_flight,
            outbound_today=outbound_today,
            recent_jobs=recent_jobs,
            reservations_today=reservations_today,
            orders_today=orders_today,
        ),
    )


@app.post("/admin/r/{rid}/profile")
async def admin_profile(
    rid: int,
    name: str = Form(...),
    cuisine: str = Form(""),
    address: str = Form(""),
    hours: str = Form(""),
    greeting: str = Form(""),
    tone: str = Form(""),
    languages: str = Form(""),
    transfer_number: str = Form(""),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    _owned(user, rid)
    update_restaurant(
        rid,
        name=name,
        cuisine=cuisine,
        address=address,
        hours=hours,
        greeting=greeting,
        tone=tone,
        languages=languages,
        transfer_number=transfer_number,
    )
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/upload")
async def admin_upload(
    rid: int,
    files: list[UploadFile] = File(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    r = _owned(user, rid)
    kdir = restaurant_dir(r["slug"]) / "knowledge"
    saved = 0
    for f in files:
        if not f.filename:
            continue
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename)
        out_path = kdir / safe_name
        with out_path.open("wb") as fh:
            shutil.copyfileobj(f.file, fh)
        saved += 1
    if saved:
        try:
            ingest_for(r["slug"])
        except Exception:
            log.exception("Ingest failed for %s", r["slug"])
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/add_url")
async def admin_add_url(
    rid: int,
    url: str = Form(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    r = _owned(user, rid)
    text = fetch_url_text(url.strip())
    if text:
        kdir = restaurant_dir(r["slug"]) / "knowledge"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", url.strip())[:80] or "url"
        out_path = kdir / f"url_{safe}.txt"
        out_path.write_text(f"Source URL: {url}\n\n{text}", encoding="utf-8")
        try:
            ingest_for(r["slug"])
        except Exception:
            log.exception("Ingest failed for %s", r["slug"])
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/add_text")
async def admin_add_text(
    rid: int,
    title: str = Form(""),
    text: str = Form(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    r = _owned(user, rid)
    body = text.strip()
    if body:
        kdir = restaurant_dir(r["slug"]) / "knowledge"
        clean = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip())[:60] or f"note_{int(__import__('time').time())}"
        out_path = kdir / f"text_{clean}.txt"
        out_path.write_text(body, encoding="utf-8")
        try:
            ingest_for(r["slug"])
        except Exception:
            log.exception("Ingest failed for %s", r["slug"])
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/test_kb")
async def admin_test_kb(
    rid: int,
    query: str = Form(...),
    user: dict = Depends(require_login),
) -> JSONResponse:
    """Quick text test of the knowledge base — same retrieval the voice agent uses."""
    r = _owned(user, rid)
    try:
        chunks = retrieve(r["slug"], query.strip(), k=4)
    except Exception as exc:
        log.exception("Test KB failed")
        return JSONResponse({"ok": False, "error": str(exc)})
    return JSONResponse({"ok": True, "answer": format_context(chunks), "chunks": chunks})


@app.post("/admin/r/{rid}/delete_file")
async def admin_delete_file(
    rid: int,
    filename: str = Form(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    r = _owned(user, rid)
    kdir = restaurant_dir(r["slug"]) / "knowledge"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    target = kdir / safe
    if target.exists() and target.is_file():
        target.unlink()
        try:
            ingest_for(r["slug"])
        except Exception:
            log.exception("Ingest failed")
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/send_forward_sms")
async def admin_send_forward_sms(
    rid: int,
    to_number: str = Form(...),
    user: dict = Depends(require_login),
) -> JSONResponse:
    r = _owned(user, rid)
    if not (r.get("twilio_account_sid") and r.get("twilio_auth_token") and r.get("twilio_number")):
        return JSONResponse({"ok": False, "error": "Activate your Twilio number first."})
    result = send_forward_sms(
        account_sid=r["twilio_account_sid"],
        auth_token=r["twilio_auth_token"],
        from_number=r["twilio_number"],
        to_number=to_number.strip(),
        forward_target=r["twilio_number"],
        restaurant_name=r.get("name") or "your restaurant",
    )
    return JSONResponse(result)


@app.post("/admin/r/{rid}/outbound/settings")
async def admin_outbound_settings(
    rid: int,
    reminder_enabled: str = Form(""),
    reminder_hours_before: int = Form(4),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    _owned(user, rid)
    update_restaurant(
        rid,
        reminder_enabled=1 if reminder_enabled else 0,
        reminder_hours_before=max(1, min(48, int(reminder_hours_before))),
    )
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/outbound/enqueue")
async def admin_outbound_enqueue(
    rid: int,
    reservation_id: int = Form(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    """Manual 'Call now' button — enqueues a reminder for a specific reservation."""
    from .db import list_reservations as _list_reservations
    from .outbound_jobs import enqueue_job

    _owned(user, rid)
    target = next((r for r in _list_reservations(rid) if r["id"] == reservation_id), None)
    if target and (target.get("phone") or "").strip():
        enqueue_job(
            restaurant_id=rid,
            job_type="reservation_reminder",
            reservation_id=target["id"],
            to_number=target["phone"].strip(),
            guest_name=target.get("name") or "",
            context={
                "guest_name": target.get("name") or "",
                "party_size": target.get("party_size"),
                "date": target.get("date"),
                "time": target.get("time"),
                "notes": target.get("notes") or "",
            },
            source="manual",
        )
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/outbound/csv")
async def admin_outbound_csv(
    rid: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    import csv
    import io

    from .outbound_jobs import enqueue_job

    _owned(user, rid)
    raw = (await file.read()).decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(raw))
    queued = 0
    for row in reader:
        phone = (row.get("phone") or "").strip()
        if not phone:
            continue
        try:
            party = int(row.get("party_size") or 0) or None
        except ValueError:
            party = None
        enqueue_job(
            restaurant_id=rid,
            job_type="reservation_reminder",
            to_number=phone,
            guest_name=(row.get("name") or "").strip(),
            context={
                "guest_name": (row.get("name") or "").strip(),
                "party_size": party,
                "date": (row.get("date") or "").strip(),
                "time": (row.get("time") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
            },
            source="csv",
        )
        queued += 1
    log.info("OUTBOUND CSV: queued %d jobs for restaurant %s", queued, rid)
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/outbound/cancel/{job_id}")
async def admin_outbound_cancel(
    rid: int,
    job_id: int,
    user: dict = Depends(require_login),
) -> RedirectResponse:
    from .outbound_jobs import cancel_job, get_job

    _owned(user, rid)
    job = get_job(job_id)
    if job and job["restaurant_id"] == rid:
        cancel_job(job_id)
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/admin/r/{rid}/outbound/rotate_secret")
async def admin_outbound_rotate_secret(
    rid: int,
    user: dict = Depends(require_login),
) -> RedirectResponse:
    from .webhook_auth import generate_secret

    _owned(user, rid)
    update_restaurant(rid, webhook_secret=generate_secret())
    return RedirectResponse(url=f"/admin/r/{rid}", status_code=303)


@app.post("/api/outbound/enqueue")
async def api_outbound_enqueue(request: Request) -> JSONResponse:
    """Public webhook to enqueue a single outbound call from an external system
    (e.g. a POS or reservation platform).

    Headers:
      X-Restaurant-Slug: <slug>
      X-Signature: hex(hmac_sha256(restaurant.webhook_secret, raw_body))

    Body (JSON):
      {
        "to_number": "+15551234567",
        "guest_name": "Sarah",                  # optional
        "reservation_id": 42,                    # optional
        "job_type": "reservation_reminder",      # default
        "scheduled_at": 1739999999,              # optional unix ts; default = now
        "context": { ... }                       # optional snapshot
      }
    """
    from .outbound_jobs import enqueue_job
    from .webhook_auth import verify

    slug = (request.headers.get("X-Restaurant-Slug") or "").strip()
    signature = (request.headers.get("X-Signature") or "").strip()
    body = await request.body()
    if not slug:
        return JSONResponse({"ok": False, "error": "missing X-Restaurant-Slug"}, status_code=400)
    restaurant = get_restaurant_by_slug(slug)
    if not restaurant:
        return JSONResponse({"ok": False, "error": "unknown restaurant"}, status_code=404)
    secret = restaurant.get("webhook_secret") or ""
    if not secret or not verify(secret, body, signature):
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)
    try:
        import json as _json
        payload = _json.loads(body or b"{}")
    except _json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    to_number = (payload.get("to_number") or "").strip()
    if not to_number:
        return JSONResponse({"ok": False, "error": "to_number required"}, status_code=400)
    job_id = enqueue_job(
        restaurant_id=restaurant["id"],
        job_type=payload.get("job_type") or "reservation_reminder",
        to_number=to_number,
        source="api",
        scheduled_at=payload.get("scheduled_at"),
        reservation_id=payload.get("reservation_id"),
        guest_name=payload.get("guest_name") or "",
        context=payload.get("context") or {},
    )
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/admin/r/{rid}/twilio")
async def admin_twilio(
    rid: int,
    twilio_account_sid: str = Form(...),
    twilio_auth_token: str = Form(...),
    twilio_number: str = Form(...),
    user: dict = Depends(require_login),
) -> JSONResponse:
    _owned(user, rid)
    twilio_number = twilio_number.strip()
    update_restaurant(
        rid,
        twilio_account_sid=twilio_account_sid.strip(),
        twilio_auth_token=twilio_auth_token.strip(),
        twilio_number=twilio_number,
    )
    result = activate_number(twilio_account_sid.strip(), twilio_auth_token.strip(), twilio_number)
    if result.get("ok"):
        update_restaurant(rid, active=1)
    return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, log_level=LOG_LEVEL.lower())
