"""Network-exposure guard: a shared token protects the app on a LAN.

This started as a personal archive reachable at ``http://<host>:7030`` with no
authentication at all, including a DELETE endpoint. The guard here is
fail-closed: once a token is configured, every HTTP route and the NiceGUI
websocket require it. Without a configured token the process refuses to serve
unless the operator explicitly opts out.

Two credential paths exist because browsers cannot attach custom headers to a
WebSocket upgrade:

* ``Authorization: Bearer <token>`` / ``Basic`` — agents, scripts, and the
  browser's initial page load.
* A derived, HttpOnly cookie issued after a successful header authentication —
  the only thing a browser can present on the websocket handshake.

The cookie is accepted for websocket scopes only, never for HTTP API calls, so a
stolen cookie cannot be replayed as a bearer token against the REST surface.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

AUTH_TOKEN_ENV = "APP_AUTH_TOKEN"
AUTH_DISABLED_ENV = "APP_AUTH_DISABLED"
REALM = "YouTube Script Viewer"
COOKIE_NAME = "ytsv_ws_auth"

# Liveness must stay unauthenticated so container health checks keep working.
EXEMPT_PATHS = frozenset({"/health"})

_UNAUTHORIZED_BODY = (
    b'{"error":{"code":"unauthorized","message":"Authentication required.","detail":{}}}'
)
_COOKIE_DERIVATION_LABEL = b"ytsv-ws-cookie-v1"


def auth_token() -> str | None:
    """Return the configured shared token, or None when auth is not configured."""
    token = (os.getenv(AUTH_TOKEN_ENV) or "").strip()
    return token or None


def auth_disabled() -> bool:
    return (os.getenv(AUTH_DISABLED_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def internal_headers() -> dict[str, str]:
    """Headers for the app's own server-side calls to its own API."""
    token = auth_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _cookie_value(token: str) -> str:
    """Derive the websocket cookie without embedding the token itself."""
    return hmac.new(token.encode("utf-8"), _COOKIE_DERIVATION_LABEL, hashlib.sha256).hexdigest()


def _decode_basic(value: str) -> str | None:
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    # Accept both "user:token" and a bare "token" payload.
    if ":" in decoded:
        return decoded.rpartition(":")[2]
    return decoded


def _header_token(scope: Scope) -> str | None:
    """Extract the token from a Bearer or Basic Authorization header."""
    raw: bytes | None = None
    for key, value in scope.get("headers") or ():
        if key.lower() == b"authorization":
            raw = value
            break
    if raw is None:
        return None
    try:
        header = raw.decode("latin-1").strip()
    except UnicodeDecodeError:
        return None
    scheme, _, rest = header.partition(" ")
    rest = rest.strip()
    if not rest:
        return None
    lowered = scheme.lower()
    if lowered == "bearer":
        return rest
    if lowered == "basic":
        return _decode_basic(rest)
    return None


def _cookie_ok(scope: Scope, token: str) -> bool:
    expected = _cookie_value(token)
    for key, value in scope.get("headers") or ():
        if key.lower() != b"cookie":
            continue
        try:
            raw = value.decode("latin-1")
        except UnicodeDecodeError:
            continue
        for part in raw.split(";"):
            name, _, val = part.strip().partition("=")
            if name == COOKIE_NAME and val and secrets.compare_digest(val, expected):
                return True
    return False


def _header_ok(scope: Scope, token: str) -> bool:
    candidate = _header_token(scope)
    return candidate is not None and secrets.compare_digest(candidate, token)


class LanAuthMiddleware:
    """Require the shared token for every HTTP request and websocket upgrade."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        token = auth_token()
        # No token configured: startup enforcement decides whether that is
        # acceptable; the middleware itself stays out of the way.
        if token is None:
            await self.app(scope, receive, send)
            return

        if (scope.get("path") or "/") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            if _header_ok(scope, token) or _cookie_ok(scope, token):
                await self.app(scope, receive, send)
                return
            # Closing before accept makes the server fail the upgrade.
            await send({"type": "websocket.close", "code": 1008})
            return

        if not _header_ok(scope, token):
            await self._unauthorized(send)
            return

        if _cookie_ok(scope, token):
            await self.app(scope, receive, send)
            return

        await self._issue_cookie_then_continue(scope, receive, send, token)

    @staticmethod
    async def _unauthorized(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", f'Basic realm="{REALM}", charset="UTF-8"'.encode()),
                    (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})

    async def _issue_cookie_then_continue(
        self, scope: Scope, receive: Receive, send: Send, token: str
    ) -> None:
        """Authenticate, then hand the browser a websocket cookie."""

        async def send_with_cookie(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append(
                    (
                        b"set-cookie",
                        (
                            f"{COOKIE_NAME}={_cookie_value(token)}; Path=/; "
                            "HttpOnly; SameSite=Lax"
                        ).encode(),
                    )
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def enforce_startup_policy() -> None:
    """Refuse to serve unprotected unless the operator opted out explicitly."""
    if auth_token() is not None or auth_disabled():
        return
    raise SystemExit(
        "Refusing to start without authentication.\n"
        f"Set {AUTH_TOKEN_ENV} to a long random string, for example:\n"
        f"  {AUTH_TOKEN_ENV}=$(openssl rand -hex 32)\n"
        f"Or, to serve the app without authentication, set {AUTH_DISABLED_ENV}=1.\n"
        "See docs/agent-api.md."
    )
