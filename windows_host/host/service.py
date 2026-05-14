from __future__ import annotations

import asyncio
import threading
from typing import Optional

from .config import HostConfig
from .server import RealtimeHost, build_app


class HostService:
    def __init__(self, config: HostConfig) -> None:
        self.config = config
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner = None
        self._host: Optional[RealtimeHost] = None
        self._started = threading.Event()
        self._start_error: Optional[BaseException] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def host(self) -> Optional[RealtimeHost]:
        return self._host

    def start(self, wait_timeout_s: float = 10.0) -> None:
        if self.is_running:
            return
        self._started.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="realtime-host-service")
        self._thread.start()
        if not self._started.wait(wait_timeout_s):
            raise TimeoutError("host_service_start_timeout")
        if self._start_error is not None:
            raise RuntimeError(str(self._start_error))

    def stop(self, wait_timeout_s: float = 10.0) -> None:
        if not self.is_running:
            return
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=wait_timeout_s)
        self._thread = None
        self._loop = None
        self._runner = None
        self._host = None
        self._started.clear()
        self._start_error = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._startup())
            self._started.set()
            loop.run_forever()
        except BaseException as exc:  # pragma: no cover - runtime guard
            self._start_error = exc
            self._started.set()
        finally:
            try:
                loop.run_until_complete(self._shutdown())
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

    async def _startup(self) -> None:
        from aiohttp import web  # lazy import keeps tests working without aiohttp

        app = build_app(config=self.config)
        self._host = app["host"]
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host=self.config.bind, port=self.config.port)
        await site.start()

    async def _shutdown(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
