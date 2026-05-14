from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any
from typing import Optional

try:
    from aiohttp import WSMsgType, web
except ModuleNotFoundError:  # pragma: no cover - fallback for constrained test environments
    from enum import Enum

    class _FallbackWebSocketResponse:
        def __init__(self, heartbeat: int = 0) -> None:
            self.heartbeat = heartbeat

        async def prepare(self, _request: Any) -> None:
            return None

        async def send_json(self, _payload: dict) -> None:
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _FallbackApplication(dict):
        def __init__(self) -> None:
            super().__init__()
            self.routes = []
            self.cleanup_ctx = []

        def add_routes(self, routes: list) -> None:
            self.routes.extend(routes)

    class _FallbackWebModule:
        Application = _FallbackApplication
        WebSocketResponse = _FallbackWebSocketResponse
        Request = object
        Response = dict

        @staticmethod
        def get(path: str, handler: Any) -> tuple[str, str, Any]:
            return ("GET", path, handler)

        @staticmethod
        def json_response(payload: dict) -> dict:
            return payload

        @staticmethod
        def run_app(_app: Any, host: str, port: int) -> None:
            raise RuntimeError(f"aiohttp is required to run server ({host}:{port})")

    class _FallbackWSMsgType(Enum):
        TEXT = "TEXT"

    WSMsgType = _FallbackWSMsgType
    web = _FallbackWebModule()

from .config import HostConfig
from .injector import TextInjector, create_default_injector
from .protocol import ProtocolError, normalize_paste_mode, parse_insert, parse_message, parse_replace
from .session import SessionManager

LOGGER = logging.getLogger("realtime_cursor_sync.host")


@dataclass
class HostStats:
    received_messages: int = 0
    ack_ok: int = 0
    ack_fail: int = 0
    dropped_messages: int = 0


@dataclass
class PendingReplace:
    session_id: str
    token: str
    seq: int
    text: str
    ws: Any
    state: dict[str, Any]
    coalesced: list[tuple[Any, dict[str, Any], int]]
    received_monotonic: float


class RealtimeHost:
    def __init__(
        self,
        config: Optional[HostConfig] = None,
        injector: Optional[TextInjector] = None,
    ) -> None:
        self.config = config or HostConfig()
        self.injector = injector or create_default_injector()
        self.session_mgr = SessionManager(timeout_ms=self.config.session_timeout_ms)
        self.stats = HostStats()
        self._inject_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rtcs-inject")
        self._replace_lock = asyncio.Lock()
        self._pending_replace: Optional[PendingReplace] = None
        self._replace_timer_task: Optional[asyncio.Task[None]] = None
        self._replace_apply_task: Optional[asyncio.Task[None]] = None
        self._app = web.Application()
        self._app.add_routes(
            [
                web.get("/ws", self.handle_ws),
                web.get("/health", self.handle_health),
            ]
        )
        self._app.cleanup_ctx.append(self._heartbeat_guard_ctx)
        self._app.cleanup_ctx.append(self._replace_lifecycle_ctx)

    @property
    def app(self) -> web.Application:
        return self._app

    async def _heartbeat_guard_ctx(self, _app: web.Application):
        task = asyncio.create_task(self._heartbeat_guard())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _replace_lifecycle_ctx(self, _app: web.Application):
        try:
            yield
        finally:
            await self.close()

    async def _heartbeat_guard(self) -> None:
        while True:
            await asyncio.sleep(max(self.config.heartbeat_interval_ms / 1000.0, 1))
            timed_out = self.session_mgr.check_timeout()
            if timed_out:
                LOGGER.warning("session timed out")

    async def handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "session_active": self.session_mgr.active is not None,
                "stats": asdict(self.stats),
            }
        )

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        # `heartbeat=0` triggers immediate timeout on newer aiohttp releases.
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        send_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sender_task = asyncio.create_task(self._sender_loop(ws, send_queue))
        state: dict[str, Any] = {"hello_ok": False, "client_id": "", "send_queue": send_queue, "closed": False}
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                self.stats.received_messages += 1
                await self._dispatch(ws, state, msg.data)
        finally:
            state["closed"] = True
            sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender_task
        return ws

    async def _sender_loop(self, ws: web.WebSocketResponse, send_queue: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            payload = await send_queue.get()
            try:
                await ws.send_json(payload)
            finally:
                send_queue.task_done()

    async def close(self) -> None:
        timer_task = self._replace_timer_task
        self._replace_timer_task = None
        if timer_task is not None:
            timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer_task
        apply_task = self._replace_apply_task
        self._replace_apply_task = None
        if apply_task is not None:
            apply_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await apply_task
        self._inject_executor.shutdown(wait=False, cancel_futures=True)

    async def _emit(self, ws: Any, state: dict[str, Any], payload: dict[str, Any]) -> None:
        if state.get("closed"):
            return
        send_queue: Optional[asyncio.Queue[dict[str, Any]]] = state.get("send_queue")
        if send_queue is None:
            await ws.send_json(payload)
            return
        await send_queue.put(payload)

    @staticmethod
    def _ack_payload(seq: int, ok: bool, reason: Optional[str] = None, *, coalesced: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "ack",
            "seq": seq,
            "applied_ts": int(time.time() * 1000),
            "ok": ok,
        }
        if reason:
            payload["reason"] = reason
        if coalesced:
            payload["coalesced"] = True
        return payload

    async def _record_and_emit_ack(
        self,
        ws: Any,
        state: dict[str, Any],
        seq: int,
        ok: bool,
        reason: Optional[str] = None,
        *,
        coalesced: bool = False,
    ) -> None:
        if ok:
            self.stats.ack_ok += 1
        else:
            self.stats.ack_fail += 1
            self.stats.dropped_messages += 1
        await self._emit(ws, state, self._ack_payload(seq, ok, reason, coalesced=coalesced))

    def _quiet_window_seconds(self) -> float:
        return max(0.0, self.config.replace_quiet_window_ms / 1000.0)

    def _schedule_replace_timer_locked(self) -> None:
        pending = self._pending_replace
        if pending is None:
            return
        if self._replace_timer_task is not None and not self._replace_timer_task.done():
            self._replace_timer_task.cancel()
        delay = max(0.0, (pending.received_monotonic + self._quiet_window_seconds()) - time.monotonic())
        self._replace_timer_task = asyncio.create_task(self._replace_timer(delay, pending.seq))

    async def _replace_timer(self, delay: float, seq: int) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._try_start_replace_apply(seq)

    async def _try_start_replace_apply(self, seq: int) -> None:
        async with self._replace_lock:
            if self._replace_apply_task is not None and not self._replace_apply_task.done():
                return
            pending = self._pending_replace
            if pending is None or pending.seq != seq:
                return
            quiet_until = pending.received_monotonic + self._quiet_window_seconds()
            if quiet_until > time.monotonic():
                self._schedule_replace_timer_locked()
                return
            request = pending
            self._pending_replace = None
            self._replace_timer_task = None
            self._replace_apply_task = asyncio.create_task(self._apply_replace_request(request))

    async def _apply_replace_request(self, request: PendingReplace) -> None:
        ok = True
        reason: Optional[str] = None
        try:
            session = self.session_mgr.validate(request.session_id, request.token)
            if not request.text.startswith(session.applied_snapshot):
                raise ValueError("non_append_edit_unsupported")
            suffix = request.text[len(session.applied_snapshot) :]
            if suffix:
                self.injector.set_paste_mode(session.paste_mode)
                await self._run_injector(self.injector.append_text, suffix)
            session.applied_snapshot = request.text
        except (PermissionError, ProtocolError, ValueError, RuntimeError) as exc:
            ok = False
            reason = str(exc)

        for old_ws, old_state, old_seq in request.coalesced:
            await self._record_and_emit_ack(old_ws, old_state, old_seq, ok, reason, coalesced=True)
        await self._record_and_emit_ack(request.ws, request.state, request.seq, ok, reason)

        async with self._replace_lock:
            if self._replace_apply_task is asyncio.current_task():
                self._replace_apply_task = None
            if self._pending_replace is not None:
                self._schedule_replace_timer_locked()

    async def _run_injector(self, fn: Any, *args: Any) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._inject_executor, lambda: fn(*args))

    async def _dispatch(self, ws: web.WebSocketResponse, state: dict[str, Any], payload: str) -> None:
        try:
            obj = parse_message(payload)
        except ProtocolError as exc:
            await self._emit(ws, state, {"type": "error", "reason": str(exc)})
            self.stats.dropped_messages += 1
            return

        msg_type = obj["type"]
        if msg_type == "hello":
            await self._on_hello(ws, state, obj)
            return
        if msg_type == "auth":
            await self._on_auth(ws, state, obj)
            return
        if msg_type == "ping":
            await self._on_ping(ws, state, obj)
            return
        if msg_type == "text_insert":
            await self._on_text_insert(ws, state, obj)
            return
        if msg_type == "text_replace":
            await self._on_text_replace(ws, state, obj)
            return
        await self._emit(ws, state, {"type": "error", "reason": "unknown_type"})
        self.stats.dropped_messages += 1

    async def _on_hello(self, ws: web.WebSocketResponse, state: dict[str, Any], obj: dict[str, Any]) -> None:
        client_id = obj.get("client_id")
        app_ver = obj.get("app_ver")
        if not isinstance(client_id, str) or not client_id:
            await self._emit(ws, state, {"type": "error", "reason": "invalid_client_id"})
            self.stats.dropped_messages += 1
            return
        if not isinstance(app_ver, str) or not app_ver:
            await self._emit(ws, state, {"type": "error", "reason": "invalid_app_ver"})
            self.stats.dropped_messages += 1
            return
        state["hello_ok"] = True
        state["client_id"] = client_id
        await self._emit(ws, state, {"type": "hello_ok"})

    async def _on_auth(self, ws: web.WebSocketResponse, state: dict[str, Any], obj: dict[str, Any]) -> None:
        if not state.get("hello_ok"):
            await self._emit(ws, state, {"type": "error", "reason": "hello_required"})
            self.stats.dropped_messages += 1
            return
        try:
            paste_mode = normalize_paste_mode(obj.get("paste_mode"), default="auto")
            session = self.session_mgr.authenticate(state["client_id"], paste_mode=paste_mode)
        except (PermissionError, ProtocolError) as exc:
            await self._emit(ws, state, {"type": "error", "reason": str(exc)})
            self.stats.dropped_messages += 1
            return
        await self._emit(
            ws,
            state,
            {
                "type": "auth_ok",
                "session_id": session.session_id,
                "token": session.token,
                "paste_mode": session.paste_mode,
                "heartbeat_interval_ms": self.config.heartbeat_interval_ms,
            },
        )

    async def _on_ping(self, ws: web.WebSocketResponse, state: dict[str, Any], obj: dict[str, Any]) -> None:
        try:
            session_id = obj["session_id"]
            token = obj["token"]
            client_ts = obj.get("ts", int(time.time() * 1000))
            if not isinstance(client_ts, int):
                raise ProtocolError("invalid_ping_ts")
            self.session_mgr.update_heartbeat(session_id, token)
            await self._emit(ws, state, {"type": "pong", "ts": client_ts, "server_ts": int(time.time() * 1000)})
        except (KeyError, PermissionError, ProtocolError) as exc:
            await self._emit(ws, state, {"type": "error", "reason": str(exc)})
            self.stats.dropped_messages += 1

    async def _on_text_insert(self, ws: web.WebSocketResponse, state: dict[str, Any], obj: dict[str, Any]) -> None:
        seq = obj.get("seq", -1)
        ack_payload: dict[str, Any] = {
            "type": "ack",
            "seq": seq if isinstance(seq, int) else -1,
            "applied_ts": int(time.time() * 1000),
            "ok": False,
        }
        try:
            insert = parse_insert(obj)
            session = self.session_mgr.expect_next_seq(insert.session_id, insert.token, insert.seq)
            self.injector.set_paste_mode(insert.paste_mode or session.paste_mode)
            await self._run_injector(self.injector.inject_text, insert.text)
            session.applied_snapshot += insert.text
            ack_payload["seq"] = insert.seq
            ack_payload["ok"] = True
            self.stats.ack_ok += 1
        except (ProtocolError, PermissionError, ValueError, RuntimeError) as exc:
            ack_payload["reason"] = str(exc)
            self.stats.ack_fail += 1
            self.stats.dropped_messages += 1
        await self._emit(ws, state, ack_payload)

    async def _on_text_replace(self, ws: web.WebSocketResponse, state: dict[str, Any], obj: dict[str, Any]) -> None:
        try:
            replace_msg = parse_replace(obj)
            self.session_mgr.expect_next_seq(replace_msg.session_id, replace_msg.token, replace_msg.seq)
        except (ProtocolError, PermissionError, ValueError, RuntimeError) as exc:
            seq = obj.get("seq", -1)
            await self._record_and_emit_ack(
                ws,
                state,
                seq if isinstance(seq, int) else -1,
                False,
                str(exc),
            )
            return

        request = PendingReplace(
            session_id=replace_msg.session_id,
            token=replace_msg.token,
            seq=replace_msg.seq,
            text=replace_msg.text,
            ws=ws,
            state=state,
            coalesced=[],
            received_monotonic=time.monotonic(),
        )
        async with self._replace_lock:
            if self._pending_replace is not None:
                request.coalesced.extend(self._pending_replace.coalesced)
                request.coalesced.append((self._pending_replace.ws, self._pending_replace.state, self._pending_replace.seq))
            self._pending_replace = request
            self._schedule_replace_timer_locked()


def build_app(config: Optional[HostConfig] = None, injector: Optional[TextInjector] = None) -> web.Application:
    host = RealtimeHost(config=config, injector=injector)
    app = host.app
    app["host"] = host
    return app
