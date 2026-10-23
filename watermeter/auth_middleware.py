"""Pure-ASGI default-deny gate for the single-admin login.

Every HTTP request is refused unless the credential store state allows its path or it carries a valid session
cookie. The gate runs before routing, so static files, unknown paths and any future router are covered without
per-route dependencies. Refusals are shaped by caller: a 303 for pages, a 401 with ``HX-Redirect`` for htmx and a JSON
401 for ``/api/*``.
"""

import logging
import os
import time
from urllib.parse import quote, urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import auth

logger = logging.getLogger(__name__)

_UNAVAILABLE_MESSAGE = "Authentication store unavailable; see the container log"
_store_error_logged = False
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_allowed(state: auth.AuthState, path: str) -> bool:
    """Exact, case-sensitive allowlist per store state. The HTTP method never matters."""
    if path == auth.HEALTH_PATH or path.startswith(auth.STATIC_PREFIX):
        return state in (auth.AuthState.UNCONFIGURED, auth.AuthState.CONFIGURED)
    if state is auth.AuthState.UNCONFIGURED:
        return path == auth.SETUP_PATH
    if state is auth.AuthState.CONFIGURED:
        return path in (auth.LOGIN_PATH, auth.LOGOUT_PATH)
    return False


def _current_state() -> auth.AuthState:
    """Store state; any failure of the store itself fails closed as UNAVAILABLE."""
    global _store_error_logged
    try:
        return auth.get_store().state()
    except Exception:
        if not _store_error_logged:
            _store_error_logged = True
            logger.exception("Auth store raised; refusing every request except /health")
        return auth.AuthState.UNAVAILABLE


def _allowed_origins() -> frozenset[str]:
    """Extra origins accepted by the Origin check (reverse proxies that rewrite Host), read on every call."""
    raw = os.environ.get(auth.ALLOWED_ORIGINS_ENV, "")
    return frozenset(item.strip().rstrip("/").lower() for item in raw.split(",") if item.strip().strip("/"))


def _csrf_refusal(headers: Headers, method: str, path: str, has_session: bool) -> str | None:
    """Reason an unsafe request is refused as cross-site, or None. A pure function of the request's own headers."""
    if method not in _UNSAFE_METHODS:
        return None
    fetch_site = headers.get("sec-fetch-site")
    origin = headers.get("origin")
    if fetch_site is not None:
        # same-site is refused on purpose: another port on the same host is same-site but not our origin.
        if fetch_site not in ("same-origin", "none"):
            return f"Sec-Fetch-Site {fetch_site}"
    elif origin is not None:
        if origin == "null":
            return "Origin null"
        if origin.rstrip("/").lower() not in _allowed_origins():
            # Netloc only, not the scheme, so a TLS proxy that does not forward X-Forwarded-Proto still works.
            # X-Forwarded-Host is never read: only the real Host header counts.
            try:
                netloc = urlsplit(origin).netloc.lower()
            except ValueError:
                return "Origin unparseable"
            if not netloc or netloc != headers.get("host", "").lower():
                return f"Origin {origin} does not match Host"
    # Neither header: a non-browser client, allowed by this step.
    if has_session and path.startswith("/api/") and headers.get(auth.CSRF_HEADER) != auth.CSRF_HEADER_VALUE:
        return f"missing {auth.CSRF_HEADER}"
    return None


def _path_with_query(scope: Scope) -> str:
    path = scope["path"]
    query = scope.get("query_string") or b""
    if query:
        path += "?" + query.decode("latin-1")
    return path


async def _deny(scope: Scope, receive: Receive, send: Send, headers: Headers, path: str, state: auth.AuthState) -> None:
    """Refuse an anonymous request in the shape its caller can handle (AUTH-07)."""
    unconfigured = state is auth.AuthState.UNCONFIGURED
    response: Response
    if headers.get("hx-request") == "true":
        # htmx processes HX-Redirect before it looks at the status, so the browser navigates and the empty body is
        # never swapped into a fragment. The target comes from the page being viewed, never from the polled fragment.
        if unconfigured:
            location = auth.SETUP_PATH
        else:
            page = auth.next_from_hx_current_url(headers.get("hx-current-url"), headers.get("host"))
            location = f"{auth.LOGIN_PATH}?next={quote(page, safe='')}"
        response = Response(status_code=401, headers={"HX-Redirect": location, "Cache-Control": "no-store"})
    elif path.startswith("/api/"):
        response = JSONResponse(
            {"success": False, "message": "Authentication required", "setup_required": unconfigured}, status_code=401
        )
    else:
        if unconfigured:
            location = auth.SETUP_PATH
        else:
            location = f"{auth.LOGIN_PATH}?next={quote(auth.safe_next(_path_with_query(scope)), safe='')}"
        response = RedirectResponse(location, status_code=303)
    await response(scope, receive, send)


class AuthMiddleware:
    """Default-deny gate; see the module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # The app has no websockets, so default deny applies.
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return

        path = scope["path"]
        headers = Headers(scope=scope)
        state = _current_state()

        if state is auth.AuthState.UNAVAILABLE:
            if path == auth.HEALTH_PATH:
                await self.app(scope, receive, send)
                return
            response: Response
            if path.startswith("/api/"):
                response = JSONResponse({"success": False, "message": _UNAVAILABLE_MESSAGE}, status_code=503)
            else:
                response = PlainTextResponse(_UNAVAILABLE_MESSAGE, status_code=503)
            await response(scope, receive, send)
            return

        issued = None
        record = None
        if state is auth.AuthState.CONFIGURED:
            record = auth.get_store().record()
            issued = auth.session_issued_at(record, HTTPConnection(scope).cookies)

        method = scope["method"]
        reason = _csrf_refusal(headers, method, path, issued is not None)
        if reason is not None:
            client = scope.get("client")
            logger.warning(f"Refused {method} {path}: {reason} (client {client[0] if client else '-'})")
            response = JSONResponse({"success": False, "message": "Cross-site request refused"}, status_code=403)
            await response(scope, receive, send)
            return

        if issued is not None and time.time() - issued > auth.SESSION_REFRESH_AFTER:
            send = self._refreshing_send(scope, send, record)

        if _is_allowed(state, path) or issued is not None:
            await self.app(scope, receive, send)
            return

        await _deny(scope, receive, send, headers, path, state)

    @staticmethod
    def _refreshing_send(scope: Scope, send: Send, record: auth.AuthRecord) -> Send:
        """Wrap ``send`` so the response carries a fresh session cookie unless it already sets that cookie."""
        secure = scope.get("scheme") == "https"

        async def send_with_refresh(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                prefix = record.cookie_name + "="
                if not any(value.startswith(prefix) for value in response_headers.getlist("set-cookie")):
                    cookie = auth.build_session_cookie(record, auth.issue_token(record), secure)
                    response_headers.append("set-cookie", cookie)
            await send(message)

        return send_with_refresh
