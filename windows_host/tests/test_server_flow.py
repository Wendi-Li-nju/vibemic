from __future__ import annotations

import asyncio
import time
import threading
import unittest

from host.config import HostConfig
from host.injector import MockInjector
from host.server import RealtimeHost


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class BlockingInjector(MockInjector):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def append_text(self, text: str) -> None:
        self.started.set()
        self.release.wait(timeout=2)
        super().append_text(text)


class PasteModeTrackingInjector(MockInjector):
    def __init__(self) -> None:
        super().__init__()
        self.paste_modes: list[str] = []
        self.current_mode = "auto"

    def set_paste_mode(self, mode: str) -> None:
        self.current_mode = mode
        self.paste_modes.append(mode)


class ServerFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.injector = MockInjector()
        self.host = RealtimeHost(
            config=HostConfig(
                bind="127.0.0.1",
                port=8765,
                heartbeat_interval_ms=200,
                session_timeout_ms=1000,
                replace_quiet_window_ms=20,
            ),
            injector=self.injector,
        )
        self.ws = FakeWebSocket()
        self.state = {"hello_ok": False, "client_id": ""}

    async def asyncTearDown(self) -> None:
        await self.host.close()

    async def _auth(self) -> dict:
        await self.host._dispatch(self.ws, self.state, '{"type":"hello","client_id":"android-test","app_ver":"1.0.0"}')
        self.assertEqual(self.ws.sent[-1]["type"], "hello_ok")
        await self.host._dispatch(self.ws, self.state, '{"type":"auth"}')
        auth_ok = self.ws.sent[-1]
        self.assertEqual(auth_ok["type"], "auth_ok")
        return auth_ok

    async def _auth_with_paste_mode(self, paste_mode: str) -> dict:
        await self.host._dispatch(self.ws, self.state, '{"type":"hello","client_id":"android-test","app_ver":"1.0.0"}')
        self.assertEqual(self.ws.sent[-1]["type"], "hello_ok")
        await self.host._dispatch(self.ws, self.state, '{"type":"auth","paste_mode":"%s"}' % paste_mode)
        auth_ok = self.ws.sent[-1]
        self.assertEqual(auth_ok["type"], "auth_ok")
        return auth_ok

    async def _wait_for_sent_count(self, expected: int) -> None:
        async def _poll() -> None:
            while len(self.ws.sent) < expected:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(_poll(), timeout=2)

    async def test_end_to_end_insert_and_ack(self) -> None:
        auth_ok = await self._auth()
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_insert","session_id":"%s","token":"%s","seq":1,"text":"a","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        ack = self.ws.sent[-1]
        self.assertTrue(ack["ok"])
        self.assertEqual(self.injector.applied, ["a"])

    async def test_auth_returns_requested_paste_mode(self) -> None:
        auth_ok = await self._auth_with_paste_mode("ctrl_shift_v")
        self.assertEqual(auth_ok["paste_mode"], "ctrl_shift_v")

    async def test_insert_uses_session_paste_mode(self) -> None:
        injector = PasteModeTrackingInjector()
        host = RealtimeHost(
            config=HostConfig(
                bind="127.0.0.1",
                port=8765,
                heartbeat_interval_ms=200,
                session_timeout_ms=1000,
                replace_quiet_window_ms=20,
            ),
            injector=injector,
        )
        ws = FakeWebSocket()
        state = {"hello_ok": False, "client_id": ""}
        try:
            await host._dispatch(ws, state, '{"type":"hello","client_id":"android-test","app_ver":"1.0.0"}')
            await host._dispatch(ws, state, '{"type":"auth","paste_mode":"ctrl_shift_v"}')
            auth_ok = ws.sent[-1]
            await host._dispatch(
                ws,
                state,
                (
                    '{"type":"text_insert","session_id":"%s","token":"%s","seq":1,"text":"a","ts":%d}'
                    % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
                ),
            )
            self.assertEqual(injector.paste_modes, ["ctrl_shift_v"])
        finally:
            await host.close()

    async def test_insert_allows_per_message_paste_mode_override(self) -> None:
        injector = PasteModeTrackingInjector()
        host = RealtimeHost(
            config=HostConfig(
                bind="127.0.0.1",
                port=8765,
                heartbeat_interval_ms=200,
                session_timeout_ms=1000,
                replace_quiet_window_ms=20,
            ),
            injector=injector,
        )
        ws = FakeWebSocket()
        state = {"hello_ok": False, "client_id": ""}
        try:
            await host._dispatch(ws, state, '{"type":"hello","client_id":"android-test","app_ver":"1.0.0"}')
            await host._dispatch(ws, state, '{"type":"auth","paste_mode":"ctrl_v"}')
            auth_ok = ws.sent[-1]
            await host._dispatch(
                ws,
                state,
                (
                    '{"type":"text_insert","session_id":"%s","token":"%s","seq":1,"text":"a","paste_mode":"ctrl_shift_v","ts":%d}'
                    % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
                ),
            )
            self.assertEqual(injector.paste_modes, ["ctrl_shift_v"])
        finally:
            await host.close()

    async def test_out_of_order_rejected(self) -> None:
        auth_ok = await self._auth()
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_insert","session_id":"%s","token":"%s","seq":2,"text":"x","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        ack = self.ws.sent[-1]
        self.assertFalse(ack["ok"])
        self.assertEqual(ack["reason"], "out_of_order")

    async def test_timeout_expires_session(self) -> None:
        auth_ok = await self._auth()
        self.host.session_mgr.active.last_seen_ms -= 2000  # type: ignore[union-attr]
        self.host.session_mgr.check_timeout()
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_insert","session_id":"%s","token":"%s","seq":1,"text":"a","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        ack = self.ws.sent[-1]
        self.assertFalse(ack["ok"])
        self.assertEqual(ack["reason"], "no_active_session")

    async def test_1000_char_stream(self) -> None:
        auth_ok = await self._auth()
        for seq in range(1, 1001):
            char = chr(ord("a") + ((seq - 1) % 26))
            await self.host._dispatch(
                self.ws,
                self.state,
                (
                    '{"type":"text_insert","session_id":"%s","token":"%s","seq":%d,"text":"%s","ts":%d}'
                    % (auth_ok["session_id"], auth_ok["token"], seq, char, int(time.time() * 1000))
                ),
            )
            ack = self.ws.sent[-1]
            if not ack["ok"]:
                self.fail(f"seq {seq} failed with reason={ack.get('reason')}")
        self.assertEqual(len(self.injector.applied), 1000)
        expected = [chr(ord("a") + (i % 26)) for i in range(1000)]
        self.assertEqual(self.injector.applied, expected)

    async def test_text_replace_rejects_non_append_edit(self) -> None:
        auth_ok = await self._auth()
        before = len(self.ws.sent)
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":1,"text":"ab","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self._wait_for_sent_count(before + 1)
        ack1 = self.ws.sent[-1]
        self.assertTrue(ack1["ok"])
        self.assertEqual(self.injector.applied, ["ab"])
        before = len(self.ws.sent)
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":2,"text":"aXb","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self._wait_for_sent_count(before + 1)
        ack2 = self.ws.sent[-1]
        self.assertFalse(ack2["ok"])
        self.assertEqual(ack2["reason"], "non_append_edit_unsupported")
        self.assertEqual(self.injector.applied, ["ab"])
        self.assertEqual(self.injector.replaced, [])

    async def test_text_replace_append_uses_suffix_after_quiet_window(self) -> None:
        auth_ok = await self._auth()
        before = len(self.ws.sent)
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":1,"text":"ab","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self._wait_for_sent_count(before + 1)
        self.assertTrue(self.ws.sent[-1]["ok"])

        before = len(self.ws.sent)
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":2,"text":"abc","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self._wait_for_sent_count(before + 1)
        self.assertTrue(self.ws.sent[-1]["ok"])
        self.assertEqual(self.injector.applied, ["ab", "c"])
        self.assertEqual(self.injector.replaced, [])

    async def test_text_replace_bursts_apply_only_latest_snapshot_after_quiet_window(self) -> None:
        auth_ok = await self._auth()
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":1,"text":"a","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":2,"text":"ab","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":3,"text":"abc","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self._wait_for_sent_count(5)

        ack_seqs = [payload["seq"] for payload in self.ws.sent if payload["type"] == "ack"]
        self.assertEqual(ack_seqs, [1, 2, 3])
        self.assertTrue(self.ws.sent[-2]["coalesced"])
        self.assertTrue(self.ws.sent[-3]["coalesced"])
        self.assertEqual(self.injector.applied, ["abc"])
        self.assertEqual(self.injector.replaced, [])

    async def test_text_replace_coalesces_pending_snapshots(self) -> None:
        self.injector = BlockingInjector()
        await self.host.close()
        self.host = RealtimeHost(
            config=HostConfig(
                bind="127.0.0.1",
                port=8765,
                heartbeat_interval_ms=200,
                session_timeout_ms=1000,
                replace_quiet_window_ms=20,
            ),
            injector=self.injector,
        )
        auth_ok = await self._auth()

        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":1,"text":"a","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await asyncio.to_thread(self.injector.started.wait, 1)

        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":2,"text":"ab","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )
        await self.host._dispatch(
            self.ws,
            self.state,
            (
                '{"type":"text_replace","session_id":"%s","token":"%s","seq":3,"text":"abc","ts":%d}'
                % (auth_ok["session_id"], auth_ok["token"], int(time.time() * 1000))
            ),
        )

        self.injector.release.set()
        await self._wait_for_sent_count(5)

        ack_seqs = [payload["seq"] for payload in self.ws.sent if payload["type"] == "ack"]
        self.assertEqual(ack_seqs, [1, 2, 3])
        self.assertTrue(self.ws.sent[-2]["coalesced"])
        self.assertEqual(self.injector.applied, ["a", "bc"])
        self.assertEqual(self.injector.replaced, [])


if __name__ == "__main__":
    unittest.main()
