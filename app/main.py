"""FastAPI app: public signup/login + per-owner admin + Twilio voice routes."""
from __future__ import annotations

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
    create_restaurant,
    create_user,
    get_restaurant,
    get_restaurant_by_number,
    get_restaurant_by_slug,
    get_user_by_email,
    init_db,
    list_orders,
    list_reservations,
    restaurant_dir,
    update_restaurant,
)
from .forwarding_codes import all_for as forwarding_all_for
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


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("DB ready. PUBLIC_HOST=%s", PUBLIC_HOST or "(not set)")


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
    restaurant = get_restaurant_by_number(to_number)
    if not restaurant:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say>This number is not configured for the AI receptionist yet. Goodbye.</Say></Response>"""
        return Response(content=twiml, media_type="application/xml")
    ws_url = f"wss://{host}/voice/stream/{restaurant['slug']}"
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
    try:
        await run_bridge(ws, restaurant=restaurant)
    except Exception:
        log.exception("Bridge crashed")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


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
