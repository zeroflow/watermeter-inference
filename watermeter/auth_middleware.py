"""Pure-ASGI default-deny gate for the single-admin login.

Every HTTP request is refused unless the credential store state allows its path or it carries a valid session
cookie. The gate runs before routing, so static files, unknown paths and any future router are covered without
per-route dependencies. Refusals are shaped by caller: a 303 for pages, a 401 with ``HX-Redirect`` for htmx and a JSON
401 for ``/api/*``.
"""

import logging
from urllib.parse import quote

from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from . import auth

logger = logging.getLogger(__name__)

_UNAVAILABLE_MESSAGE = "Authentication store unavailable; see the container log"
_store_error_logged = False


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
        if state is auth.AuthState.CONFIGURED:
            record = auth.get_store().record()
            issued = auth.session_issued_at(record, HTTPConnection(scope).cookies)

        if _is_allowed(state, path) or issued is not None:
            await self.app(scope, receive, send)
            return

        await _deny(scope, receive, send, headers, path, state)
