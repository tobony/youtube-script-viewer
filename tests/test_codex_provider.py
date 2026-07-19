import asyncio

import pytest

from app.codex_provider import CodexAppServerClient, CodexProviderError, _find_codex_binary


def test_missing_codex_binary_is_reported():
    client = CodexAppServerClient(codex_bin="")
    client.codex_bin = None

    assert client.available is False


def test_find_codex_binary_prefers_configured_path(monkeypatch):
    monkeypatch.setenv("CODEX_BIN", r"C:\tools\codex.exe")

    assert _find_codex_binary() == r"C:\tools\codex.exe"


@pytest.mark.asyncio
async def test_reader_routes_responses_and_notifications():
    client = CodexAppServerClient(codex_bin="codex")
    response_future = asyncio.get_running_loop().create_future()
    client._pending[1] = response_future
    queue = asyncio.Queue()
    client._subscribers.add(queue)

    class FakeStdout:
        def __init__(self):
            self.lines = iter(
                [
                    b'{"id":1,"result":{"ok":true}}\n',
                    b'{"method":"turn/completed","params":{"turn":{"id":"turn-1","status":"completed"}}}\n',
                ]
            )

        async def readline(self):
            return next(self.lines, b"")

    class FakeProcess:
        stdout = FakeStdout()

    client._process = FakeProcess()
    await client._reader_loop()

    assert (await response_future)["result"] == {"ok": True}
    notification = await queue.get()
    assert notification["method"] == "turn/completed"


@pytest.mark.asyncio
async def test_request_requires_running_process():
    client = CodexAppServerClient(codex_bin="codex")
    client.start = lambda: asyncio.sleep(0)

    with pytest.raises(CodexProviderError, match="실행 중이 아닙니다"):
        await client._request("account/read")


@pytest.mark.asyncio
async def test_list_models_normalizes_app_server_response():
    client = CodexAppServerClient(codex_bin="codex")

    async def fake_request(method, params=None, **kwargs):
        assert method == "model/list"
        return {
            "data": [
                {"id": "gpt-5.6-terra", "displayName": "GPT-5.6-Terra", "isDefault": False},
                {"id": "gpt-5.6-luna", "displayName": "GPT-5.6-Luna", "isDefault": False},
            ]
        }

    client._request = fake_request

    assert await client.list_models() == [
        {"id": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "is_default": False},
        {"id": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "is_default": False},
    ]


@pytest.mark.asyncio
async def test_run_prompt_uses_current_read_only_sandbox_schema():
    client = CodexAppServerClient(codex_bin="codex")
    seen = {}

    async def fake_start():
        return None

    async def fake_request(method, params=None, **kwargs):
        seen[method] = params
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            queue = next(iter(client._subscribers))
            queue.put_nowait(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {"type": "agentMessage", "phase": "final_answer", "text": "OK"},
                    },
                }
            )
            queue.put_nowait(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                }
            )
            return {"turn": {"id": "turn-1"}}
        return {}

    client.start = fake_start
    client._request = fake_request

    result = await client.run_prompt("Reply OK", model="gpt-test")

    assert result == "OK"
    assert seen["thread/start"]["sandbox"] == "read-only"
    assert seen["turn/start"]["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
