from __future__ import annotations

import unittest

from host.session import SessionManager


class SessionTests(unittest.TestCase):
    def test_authenticate_and_validate(self) -> None:
        manager = SessionManager(timeout_ms=1000)
        session = manager.authenticate("client-a")
        validated = manager.validate(session.session_id, session.token)
        self.assertEqual(validated.client_id, "client-a")
        self.assertEqual(validated.paste_mode, "auto")

    def test_authenticate_stores_requested_paste_mode(self) -> None:
        manager = SessionManager(timeout_ms=1000)
        session = manager.authenticate("client-a", paste_mode="ctrl_shift_v")
        self.assertEqual(session.paste_mode, "ctrl_shift_v")

    def test_new_auth_evicts_old_session(self) -> None:
        manager = SessionManager(timeout_ms=1000)
        first = manager.authenticate("client-a")
        second = manager.authenticate("client-b")
        self.assertNotEqual(first.session_id, second.session_id)
        with self.assertRaises(PermissionError):
            manager.validate(first.session_id, first.token)
        self.assertEqual(manager.validate(second.session_id, second.token).client_id, "client-b")

    def test_timeout_clears_session(self) -> None:
        manager = SessionManager(timeout_ms=1000)
        session = manager.authenticate("client-a")
        manager.active.last_seen_ms -= 2000  # type: ignore[union-attr]
        self.assertTrue(manager.check_timeout())
        with self.assertRaises(PermissionError):
            manager.validate(session.session_id, session.token)


if __name__ == "__main__":
    unittest.main()
