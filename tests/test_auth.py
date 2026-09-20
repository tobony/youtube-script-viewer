"""Network guard tests: the app must not serve unprotected on a LAN."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DB_PATH"] = "data/test_auth.db"

import app.db as db_module
from app.auth import COOKIE_NAME, LanAuthMiddleware, _cookie_value, enforce_startup_policy
from app.db import init_db
from app.main import app

TEST_DB_PATH = "data/test_auth.db"
TOKEN = "test-token-0123456789abcdef"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
async def setup_db(monkeypatch):
    previous = db_module.DB_PATH
    db_module.DB_PATH = TEST_DB_PATH
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    await init_db()
    # conftest disables auth globally so existing suites stay unchanged; each
    # test here chooses its own configuration.
    monkeypatch.delenv("APP_AUTH_DISABLED", raising=False)
    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    db_module.DB_PATH = previous


@pytest.mark.asyncio
async def test_health_is_exempt_so_container_checks_keep_working(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.get("/health")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_existing_ui_api_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.get("/api/analyses")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_existing_ui_delete_requires_token_when_configured(monkeypatch):
    """The unauthenticated DELETE surface is the reason this guard exists."""
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.delete("/api/analyses/does-not-exist")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bearer_token_is_accepted(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.get("/api/analyses", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_basic_auth_is_accepted(monkeypatch):
    """Browsers use Basic so the operator can type the token once."""
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.get("/api/analyses", auth=("viewer", TOKEN))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_wrong_token_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.get("/api/analyses", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_response_issues_websocket_cookie(monkeypatch):
    """The browser gets the websocket cookie from its first authenticated call.

    Rendering the NiceGUI page itself is outside this suite's scope, so the
    cookie handoff is asserted on an API route.
    """
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.get("/api/analyses", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert COOKIE_NAME in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_cookie_is_not_reissued_when_already_present(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    cookie = f"{COOKIE_NAME}={_cookie_value(TOKEN)}"
    async with _client() as c:
        r = await c.get(
            "/api/analyses",
            headers={"Authorization": f"Bearer {TOKEN}", "Cookie": cookie},
        )
    assert r.status_code == 200
    assert COOKIE_NAME not in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_startup_refuses_to_serve_without_token_or_optout(monkeypatch):
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("APP_AUTH_DISABLED", raising=False)
    with pytest.raises(SystemExit):
        enforce_startup_policy()


@pytest.mark.asyncio
async def test_startup_allows_explicit_optout(monkeypatch):
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("APP_AUTH_DISABLED", "1")
    enforce_startup_policy()


@pytest.mark.asyncio
async def test_websocket_upgrade_without_credential_is_closed(monkeypatch):
    """Browsers cannot set headers on a websocket handshake, so it must gate."""
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    sent: list[dict] = []

    async def inner(scope, receive, send):  # pragma: no cover - should not run
        sent.append({"type": "upgraded"})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "websocket.connect"}

    middleware = LanAuthMiddleware(inner)
    await middleware(
        {"type": "websocket", "path": "/_nicegui_ws/", "headers": []}, receive, send
    )
    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
async def test_websocket_upgrade_accepts_derived_cookie(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    sent: list[dict] = []

    async def inner(scope, receive, send):
        sent.append({"type": "upgraded"})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "websocket.connect"}

    cookie = f"{COOKIE_NAME}={_cookie_value(TOKEN)}".encode()
    middleware = LanAuthMiddleware(inner)
    await middleware(
        {"type": "websocket", "path": "/_nicegui_ws/", "headers": [(b"cookie", cookie)]},
        receive,
        send,
    )
    assert sent == [{"type": "upgraded"}]


@pytest.mark.asyncio
async def test_websocket_cookie_does_not_leak_the_token(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    value = _cookie_value(TOKEN)
    assert TOKEN not in value
    assert len(value) == 64


@pytest.mark.asyncio
async def test_cookie_is_not_accepted_for_http_api(monkeypatch):
    """A stolen cookie must not work as a bearer token on the REST surface."""
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    cookie = f"{COOKIE_NAME}={_cookie_value(TOKEN)}"
    async with _client() as c:
        r = await c.get("/api/analyses", headers={"Cookie": cookie})
    assert r.status_code == 401
