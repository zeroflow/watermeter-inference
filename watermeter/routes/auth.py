"""Setup, login, logout, account and password-change routes for the single admin."""

import asyncio
import logging
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import auth
from ..ratelimit import FailureLimiter
from .pages import register_template_globals

logger = logging.getLogger(__name__)

router = APIRouter()

# Own template instance so a test stubbing the pages templates never changes how these pages render.
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
register_template_globals(templates)

# Failure-only throttle shared by the login, setup token and password change checks (AUTH-11).
limiter = FailureLimiter()

# Setup and login forms carry a handful of short fields; anything larger is an attack or a mistake (T-01-21).
MAX_FORM_BYTES = 16384


def _client_ip(request: Request) -> str:
    """Peer address of the connection. X-Forwarded-For is never trusted: any client could forge it."""
    return request.client.host if request.client else "unknown"


def _is_secure(request: Request) -> bool:
    return request.url.scheme == "https"


def _is_authenticated(request: Request, record: auth.AuthRecord | None) -> bool:
    return auth.session_issued_at(record, request.cookies) is not None


def _is_upgrade() -> bool:
    """True when the data directory already holds meter data, i.e. this instance ran before auth existed (D-06)."""
    data_dir = auth.auth_file_path().parent
    return (data_dir / "reference_raw.jpg").exists() or (data_dir / "state.json").exists()


async def _read_form(request: Request) -> dict[str, str] | None:
    """Parse an urlencoded form body, or None when it is larger than MAX_FORM_BYTES.

    The cap holds with and without a Content-Length header: the bytes are counted while the stream is read.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_FORM_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FORM_BYTES:
            return None
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=20)
    return {key: values[0] for key, values in parsed.items()}


def _render(request: Request, template: str, context: dict, status_code: int = 200):
    response = templates.TemplateResponse(request, template, context=context, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _too_large() -> PlainTextResponse:
    return PlainTextResponse("Request body too large", status_code=413)


def _setup_context(error: str | None) -> dict:
    return {
        "error": error,
        "token_required": auth.setup_token_required(),
        "upgrade": _is_upgrade(),
        "readme_url": auth.README_AUTH_URL,
    }


def _login_context(next_target: str, error: str | None) -> dict:
    return {"next": next_target, "error": error, "recovery_url": auth.README_RECOVERY_URL}


def _setup_closed(request: Request, state: auth.AuthState, record: auth.AuthRecord | None):
    """Answer for /setup once the instance is configured or the store is broken; never the form."""
    if state is auth.AuthState.UNAVAILABLE:
        return PlainTextResponse("Authentication is unavailable: the auth file cannot be read.", status_code=503)
    return RedirectResponse("/" if _is_authenticated(request, record) else auth.LOGIN_PATH, status_code=303)


@router.get(
    "/setup",
    response_class=HTMLResponse,
    tags=["Auth"],
    summary="First-run setup page",
    description="Render the page where the admin password is set on an unconfigured instance",
)
async def setup_page(request: Request):
    """Show the setup form while no admin password exists."""
    store = auth.get_store()
    state = store.state()
    if state is not auth.AuthState.UNCONFIGURED:
        return _setup_closed(request, state, store.record())
    return _render(request, "setup.html", _setup_context(None))


@router.post(
    "/setup",
    tags=["Auth"],
    summary="Set the admin password",
    description="Create the admin credential on an unconfigured instance and log the owner in",
)
async def setup_submit(request: Request):
    """Create the admin password, log in and go to the dashboard (D-10)."""
    form = await _read_form(request)
    if form is None:
        return _too_large()
    store = auth.get_store()
    state = store.state()
    if state is not auth.AuthState.UNCONFIGURED:
        return _setup_closed(request, state, store.record())
    ip = _client_ip(request)
    if auth.setup_token_required():
        key = f"setup:{ip}"
        wait = limiter.retry_after(key)
        if wait > 0:
            throttled = _render(
                request,
                "setup.html",
                _setup_context(f"Too many attempts, try again in {wait} s"),
                status_code=429,
            )
            throttled.headers["Retry-After"] = str(wait)
            return throttled
        if not auth.check_setup_token(form.get("token")):
            limiter.record_failure(key)
            # D-09: the owner reads the token from the container log, so it is repeated on every failed attempt.
            logger.warning(
                f"Setup attempt from {ip} with a wrong or missing setup token; "
                f"the current token is {auth.get_setup_token()}"
            )
            return _render(
                request,
                "setup.html",
                _setup_context("Wrong or missing setup token (see the container log)"),
                status_code=403,
            )
    password = form.get("password", "")
    problem = auth.validate_new_password(password, form.get("confirm", ""))
    if problem is not None:
        return _render(request, "setup.html", _setup_context(problem), status_code=400)
    password_hash = await asyncio.to_thread(auth.hash_password, password)
    created = await asyncio.to_thread(store.create, password_hash)
    record = store.record()
    if not created or record is None:
        # Someone else finished setup first (AUTH-02): never log this request in.
        return RedirectResponse(auth.LOGIN_PATH, status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.headers.append(
        "set-cookie", auth.build_session_cookie(record, auth.issue_token(record), _is_secure(request))
    )
    logger.info(f"Admin password set via the setup page from {ip}")
    return response


@router.get(
    "/login",
    response_class=HTMLResponse,
    tags=["Auth"],
    summary="Login page",
    description="Render the login form",
)
async def login_page(request: Request):
    """Show the login form, or send the visitor where they belong."""
    store = auth.get_store()
    state = store.state()
    if state is auth.AuthState.UNCONFIGURED:
        return RedirectResponse(auth.SETUP_PATH, status_code=303)
    if state is auth.AuthState.UNAVAILABLE:
        return PlainTextResponse("Authentication is unavailable: the auth file cannot be read.", status_code=503)
    next_target = auth.safe_next(request.query_params.get("next"))
    if _is_authenticated(request, store.record()):
        return RedirectResponse(next_target, status_code=303)
    return _render(request, "login.html", _login_context(next_target, None))


@router.post(
    "/login",
    tags=["Auth"],
    summary="Log in",
    description="Check the admin password and start a session",
)
async def login_submit(request: Request):
    """Verify the password. Throttled clients are refused before any hashing happens (D-08)."""
    form = await _read_form(request)
    if form is None:
        return _too_large()
    store = auth.get_store()
    state = store.state()
    if state is auth.AuthState.UNCONFIGURED:
        return RedirectResponse(auth.SETUP_PATH, status_code=303)
    record = store.record()
    if state is auth.AuthState.UNAVAILABLE or record is None:
        return PlainTextResponse("Authentication is unavailable: the auth file cannot be read.", status_code=503)
    ip = _client_ip(request)
    next_target = auth.safe_next(form.get("next"))
    # reserve() books the attempt before the awaited hash, so parallel guesses cannot all pass the check.
    wait, _ = limiter.reserve(ip)
    if wait > 0:
        throttled = _render(
            request,
            "login.html",
            _login_context(next_target, f"Too many attempts, try again in {wait} s"),
            status_code=429,
        )
        throttled.headers["Retry-After"] = str(wait)
        return throttled
    password = form.get("password", "")
    ok = False
    if len(password) <= auth.MAX_PASSWORD_LENGTH:
        ok = await asyncio.to_thread(auth.verify_password, password, record.password_hash)
    if not ok:
        logger.warning(f"Failed login from {ip}")
        return _render(request, "login.html", _login_context(next_target, "Wrong password"), status_code=401)
    limiter.reset(ip)
    logger.info(f"Login from {ip}")
    response = RedirectResponse(next_target, status_code=303)
    response.headers.append(
        "set-cookie", auth.build_session_cookie(record, auth.issue_token(record), _is_secure(request))
    )
    return response


@router.post(
    "/logout",
    tags=["Auth"],
    summary="Log out",
    description="End the session of this browser and return to the login page",
)
async def logout(request: Request):
    """Clear the session cookie of this browser."""
    response = RedirectResponse(auth.LOGIN_PATH, status_code=303)
    record = auth.get_store().record()
    if record is not None:
        response.headers.append("set-cookie", auth.build_clear_cookie(record, _is_secure(request)))
    return response


class PasswordChangeRequest(BaseModel):
    """Request body of the change-password API. Defaults keep missing fields inside the {success, message} contract."""

    current: str = ""
    new: str = ""
    confirm: str = ""


@router.get(
    "/account",
    response_class=HTMLResponse,
    tags=["Auth"],
    summary="Account page",
    description="Render the change-password form and the logout button",
)
async def account_page(request: Request):
    """Show the account page (password change and logout). No session details are shown (D-04)."""
    return _render(request, "account.html", {"nav_active": "account"})


def _password_failure(message: str, status_code: int, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"success": False, "message": message}, status_code=status_code, headers=headers)


@router.post(
    "/api/auth/password",
    tags=["Auth"],
    summary="Change the admin password",
    description="Verify the current password, store the new one and end every other session",
)
async def change_password(request: Request, body: PasswordChangeRequest):
    """Change the password; this browser gets a cookie for the new epoch and stays logged in (D-03)."""
    ip = _client_ip(request)
    key = f"pw:{ip}"
    store = auth.get_store()
    record = store.record()
    if record is None:
        return _password_failure("Authentication is unavailable", 503)
    if not body.current:
        return _password_failure("Current password is required", 400)
    # Booked before the awaited hash (see login_submit); given back when the current password is right.
    wait, stamp = limiter.reserve(key)
    if wait > 0:
        return _password_failure(f"Too many attempts, try again in {wait} s", 429, headers={"Retry-After": str(wait)})
    ok = False
    if len(body.current) <= auth.MAX_PASSWORD_LENGTH:
        ok = await asyncio.to_thread(auth.verify_password, body.current, record.password_hash)
    if not ok:
        logger.warning(f"Failed password change from {ip}")
        # 403, not 401: auth.js treats a 401 as an expired session and would send the owner to the login page.
        return _password_failure("Current password is wrong", 403)
    limiter.release(key, stamp)
    problem = auth.validate_new_password(body.new, body.confirm)
    if problem is not None:
        return _password_failure(problem, 400)
    new_hash = await asyncio.to_thread(auth.hash_password, body.new)
    try:
        new_record = await asyncio.to_thread(store.replace_password, new_hash)
    except (auth.AuthStoreError, OSError) as exc:
        logger.error(f"Could not store the new admin password: {exc}")
        return _password_failure("Could not store the new password", 500)
    limiter.reset(key)
    logger.info(f"Admin password changed from {ip}; other sessions logged out")
    response = JSONResponse({"success": True, "message": "Password changed. Other sessions were logged out."})
    response.headers.append(
        "set-cookie", auth.build_session_cookie(new_record, auth.issue_token(new_record), _is_secure(request))
    )
    return response
