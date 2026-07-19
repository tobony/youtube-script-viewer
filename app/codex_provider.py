"""Official Codex app-server adapter for ChatGPT-managed authentication."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _find_codex_binary() -> str | None:
    configured = os.getenv("CODEX_BIN", "").strip()
    if configured:
        return configured

    # The WindowsApps shim can launch the desktop app instead of preserving
    # stdio. Prefer the real versioned CLI installed by the Codex desktop app.
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if local_app_data:
        bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        candidates = list(bin_root.glob("*/codex.exe")) if bin_root.is_dir() else []
        if candidates:
            newest = max(candidates, key=lambda path: path.stat().st_mtime)
            return str(newest)
    return shutil.which("codex")


class CodexProviderError(RuntimeError):
    """A user-facing Codex app-server failure."""


class CodexAppServerClient:
    """Small async JSON-RPC client for the official ``codex app-server``."""

    def __init__(self, codex_bin: str | None = None, timeout: float | None = None):
        self.codex_bin = codex_bin or _find_codex_binary()
        self.timeout = timeout or float(os.getenv("CODEX_REQUEST_TIMEOUT", "180"))
        self.startup_timeout = float(os.getenv("CODEX_START_TIMEOUT", "15"))
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscribers: set[asyncio.Queue] = set()

    @property
    def available(self) -> bool:
        return bool(self.codex_bin)

    async def start(self) -> None:
        if self._process and self._process.returncode is None:
            return
        async with self._start_lock:
            if self._process and self._process.returncode is None:
                return
            if not self.codex_bin:
                raise CodexProviderError(
                    "Codex 실행 파일을 찾을 수 없습니다. Codex 앱 또는 CLI를 설치하거나 CODEX_BIN을 설정하세요."
                )

            try:
                self._process = await asyncio.create_subprocess_exec(
                    self.codex_bin,
                    "app-server",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (OSError, PermissionError) as exc:
                raise CodexProviderError(f"Codex app-server를 시작할 수 없습니다: {exc}") from exc

            self._reader_task = asyncio.create_task(self._reader_loop())
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            try:
                await self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "youtube_script_viewer",
                            "title": "YouTube Script Viewer",
                            "version": "0.1.0",
                        }
                    },
                    timeout=self.startup_timeout,
                    ensure_started=False,
                )
                await self._notify("initialized", {})
            except Exception:
                await self.close()
                raise

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process and process.returncode is None:
            if process.stdin:
                process.stdin.close()
            with suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(process.wait(), timeout=3)
            if process.returncode is None:
                process.terminate()
                with suppress(asyncio.TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(process.wait(), timeout=2)
            if process.returncode is None:
                process.kill()

        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task and task is not current and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._reader_task = None
        self._stderr_task = None
        self._fail_pending(CodexProviderError("Codex app-server 연결이 종료되었습니다."))

    async def account_status(self) -> dict[str, Any]:
        result = await self._request("account/read", {"refreshToken": False})
        account = result.get("account") or {}
        return {
            "available": True,
            "connected": bool(account),
            "auth_mode": account.get("type"),
            "plan_type": account.get("planType"),
            "requires_auth": bool(result.get("requiresOpenaiAuth", True)),
        }

    async def start_login(self, mode: str = "chatgpt") -> dict[str, Any]:
        login_type = "chatgptDeviceCode" if mode == "device" else "chatgpt"
        params: dict[str, Any] = {"type": login_type}
        if login_type == "chatgpt":
            params.update({"useHostedLoginSuccessPage": True, "appBrand": "chatgpt"})
        return await self._request("account/login/start", params)

    async def logout(self) -> None:
        await self._request("account/logout", {})

    async def rate_limits(self) -> dict[str, Any]:
        return await self._request("account/rateLimits/read", {})

    async def list_models(self) -> list[dict[str, Any]]:
        result = await self._request("model/list", {})
        models = result.get("data") or result.get("models") or []
        return [
            {
                "id": item.get("id") or item.get("model"),
                "display_name": item.get("displayName") or item.get("name") or item.get("id"),
                "is_default": bool(item.get("isDefault")),
            }
            for item in models
            if item.get("id") or item.get("model")
        ]

    async def run_prompt(self, prompt: str, *, system: str = "", model: str = "") -> str:
        await self.start()
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        thread_id = ""
        turn_id = ""
        completed = False
        runtime_dir = Path(tempfile.gettempdir()) / "youtube-script-viewer-codex"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        full_prompt = prompt
        if system:
            full_prompt = f"Instructions:\n{system}\n\nTask:\n{prompt}"
        full_prompt = (
            "Complete this text-only task without inspecting files, running commands, or using tools. "
            "Return only the requested final answer.\n\n" + full_prompt
        )

        try:
            thread_result = await self._request(
                "thread/start",
                {
                    "model": model,
                    "cwd": str(runtime_dir),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "serviceName": "youtube_script_viewer",
                },
            )
            thread_id = (thread_result.get("thread") or {}).get("id", "")
            if not thread_id:
                raise CodexProviderError("Codex thread를 시작하지 못했습니다.")

            turn_result = await self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": full_prompt}],
                    "cwd": str(runtime_dir),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "readOnly",
                        "networkAccess": False,
                    },
                    "model": model,
                    "effort": "low",
                },
            )
            turn_id = (turn_result.get("turn") or {}).get("id", "")
            if not turn_id:
                raise CodexProviderError("Codex turn을 시작하지 못했습니다.")

            final_text = ""
            fallback_text = ""
            delta_text = ""
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise CodexProviderError("Codex 응답 시간이 초과되었습니다.")
                message = await asyncio.wait_for(queue.get(), timeout=remaining)
                method = message.get("method")
                params = message.get("params") or {}
                message_turn_id = params.get("turnId") or (params.get("turn") or {}).get("id")
                message_thread_id = params.get("threadId")
                if message_turn_id and message_turn_id != turn_id:
                    continue
                if message_thread_id and message_thread_id != thread_id:
                    continue

                if method == "item/agentMessage/delta":
                    delta_text += params.get("delta", "")
                elif method == "item/completed":
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage" and item.get("text"):
                        fallback_text = item["text"]
                        if item.get("phase") in (None, "final_answer"):
                            final_text = item["text"]
                elif method == "error":
                    error = params.get("error") or {}
                    raise CodexProviderError(error.get("message") or "Codex 요청이 실패했습니다.")
                elif method == "turn/completed":
                    turn = params.get("turn") or {}
                    status = turn.get("status")
                    if status == "completed":
                        completed = True
                        result = (final_text or fallback_text or delta_text).strip()
                        if not result:
                            raise CodexProviderError("Codex가 빈 응답을 반환했습니다.")
                        return result
                    error = turn.get("error") or {}
                    raise CodexProviderError(error.get("message") or f"Codex turn이 {status or '실패'} 상태로 종료되었습니다.")
        finally:
            self._subscribers.discard(queue)
            if turn_id and not completed:
                with suppress(Exception):
                    await self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
            if thread_id:
                with suppress(Exception):
                    await self._request("thread/delete", {"threadId": thread_id}, timeout=5)

    async def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        ensure_started: bool = True,
    ) -> dict[str, Any]:
        if ensure_started:
            await self.start()
        process = self._process
        if not process or process.returncode is not None or not process.stdin:
            raise CodexProviderError("Codex app-server가 실행 중이 아닙니다.")

        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write_message({"method": method, "id": request_id, "params": params or {}})
            response = await asyncio.wait_for(future, timeout=timeout or self.timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise CodexProviderError(f"Codex app-server 요청 시간이 초과되었습니다: {method}") from exc
        return response.get("result") or {}

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write_message({"method": method, "params": params})

    async def _write_message(self, message: dict[str, Any]) -> None:
        process = self._process
        if not process or not process.stdin:
            raise CodexProviderError("Codex app-server에 연결할 수 없습니다.")
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            process.stdin.write(payload)
            await process.stdin.drain()

    async def _reader_loop(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Ignoring non-JSON Codex app-server output")
                    continue
                request_id = message.get("id")
                if request_id is not None and ("result" in message or "error" in message):
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        if message.get("error"):
                            error = message["error"]
                            future.set_exception(CodexProviderError(error.get("message") or str(error)))
                        else:
                            future.set_result(message)
                    continue
                for subscriber in tuple(self._subscribers):
                    subscriber.put_nowait(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Codex app-server reader stopped: %s", exc)
        finally:
            self._fail_pending(CodexProviderError("Codex app-server가 예기치 않게 종료되었습니다."))

    async def _drain_stderr(self) -> None:
        process = self._process
        if not process or not process.stderr:
            return
        try:
            while await process.stderr.readline():
                pass
        except asyncio.CancelledError:
            raise

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


class CodexAppServerManager:
    """Run the async app-server client on a Proactor-compatible worker loop.

    NiceGUI uses a Windows selector loop which cannot create subprocesses.
    Keeping the app-server and all its asyncio primitives on a dedicated loop
    also prevents cross-client UI lifecycle events from cancelling the process.
    """

    def __init__(self, codex_bin: str | None = None):
        self.codex_bin = codex_bin or _find_codex_binary()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: CodexAppServerClient | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="codex-app-server",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise CodexProviderError("Codex app-server 작업 루프를 시작하지 못했습니다.")

    @property
    def available(self) -> bool:
        return bool(self.codex_bin)

    def _run_loop(self) -> None:
        # NiceGUI installs WindowsSelectorEventLoopPolicy process-wide. A new
        # loop would therefore still inherit the subprocess-incompatible
        # selector implementation unless we explicitly request Proactor.
        loop = asyncio.ProactorEventLoop() if os.name == "nt" else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._client = CodexAppServerClient(codex_bin=self.codex_bin)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if not self._loop or not self._client or not self._thread.is_alive():
            raise CodexProviderError("Codex app-server 작업 루프가 실행 중이 아닙니다.")
        future = asyncio.run_coroutine_threadsafe(
            getattr(self._client, method)(*args, **kwargs),
            self._loop,
        )
        return await asyncio.wrap_future(future)

    async def account_status(self) -> dict[str, Any]:
        return await self._call("account_status")

    async def start_login(self, mode: str = "chatgpt") -> dict[str, Any]:
        return await self._call("start_login", mode)

    async def logout(self) -> None:
        await self._call("logout")

    async def rate_limits(self) -> dict[str, Any]:
        return await self._call("rate_limits")

    async def list_models(self) -> list[dict[str, Any]]:
        return await self._call("list_models")

    async def run_prompt(self, prompt: str, *, system: str = "", model: str = "") -> str:
        return await self._call("run_prompt", prompt, system=system, model=model)

    async def close(self) -> None:
        if not self._loop or not self._thread.is_alive():
            return
        with suppress(Exception):
            await self._call("close")
        self._loop.call_soon_threadsafe(self._loop.stop)
        await asyncio.to_thread(self._thread.join, 5)
        self._loop = None
        self._client = None


_client: CodexAppServerManager | None = None


def get_codex_client() -> CodexAppServerManager:
    global _client
    if _client is None:
        _client = CodexAppServerManager()
    return _client


async def close_codex_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
