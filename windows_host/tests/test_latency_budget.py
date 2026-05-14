from __future__ import annotations

import statistics
import time
import unittest

from host.config import HostConfig
from host.injector import MockInjector
from host.server import RealtimeHost


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class LatencyBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_dispatch_latency_budget(self) -> None:
        injector = MockInjector()
        host = RealtimeHost(
            config=HostConfig(bind="127.0.0.1", port=8765, heartbeat_interval_ms=5000, session_timeout_ms=15000),
            injector=injector,
        )
        ws = FakeWebSocket()
        state = {"hello_ok": False, "client_id": ""}
        await host._dispatch(ws, state, '{"type":"hello","client_id":"android-test","app_ver":"1.0.0"}')
        await host._dispatch(ws, state, '{"type":"auth"}')
        auth_ok = ws.sent[-1]

        durations_ms = []
        for seq in range(1, 1001):
            char = chr(ord("a") + ((seq - 1) % 26))
            payload = (
                '{"type":"text_insert","session_id":"%s","token":"%s","seq":%d,"text":"%s","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], seq, char, int(time.time() * 1000))
            )
            started = time.perf_counter()
            await host._dispatch(ws, state, payload)
            durations_ms.append((time.perf_counter() - started) * 1000.0)

        median_ms = statistics.median(durations_ms)
        p99_ms = sorted(durations_ms)[int(len(durations_ms) * 0.99) - 1]
        self.assertLess(median_ms, 80.0, f"median latency {median_ms:.2f}ms exceeds 80ms")
        self.assertLess(p99_ms, 200.0, f"p99 latency {p99_ms:.2f}ms exceeds 200ms")


if __name__ == "__main__":
    unittest.main()
