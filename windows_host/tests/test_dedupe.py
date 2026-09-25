from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from host.dedupe import AppliedOpLedger


class AppliedOpLedgerTests(unittest.TestCase):
    def test_memory_ledger_dedupes_matching_text(self) -> None:
        ledger = AppliedOpLedger()
        self.assertEqual(ledger.status("client", "op-1", "hello"), "missing")
        self.assertTrue(ledger.record("client", "op-1", "hello"))
        self.assertTrue(ledger.contains("client", "op-1"))
        self.assertEqual(ledger.status("client", "op-1", "hello"), "match")
        self.assertEqual(ledger.status("client", "op-1", "different"), "conflict")
        self.assertFalse(ledger.contains("other-client", "op-1"))

    def test_persistent_ledger_survives_reload_with_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "applied.json"
            first = AppliedOpLedger(path=path)
            self.assertTrue(first.record("client", "op-1", "payload"))

            second = AppliedOpLedger(path=path)
            self.assertEqual(second.status("client", "op-1", "payload"), "match")
            self.assertEqual(second.status("client", "op-1", "other"), "conflict")

    def test_legacy_key_only_ledger_loads_as_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "applied.json"
            path.write_text(json.dumps(["client\u001fop-1"]))
            ledger = AppliedOpLedger(path=path)
            self.assertEqual(ledger.status("client", "op-1", "unknown-old-text"), "match")

    def test_corrupt_ledger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "applied.json"
            path.write_text("{not-json")
            ledger = AppliedOpLedger(path=path)
            self.assertTrue(ledger.unavailable_reason)
            self.assertEqual(ledger.status("client", "op-1", "text"), "unavailable")

    def test_persist_failure_marks_ledger_unavailable(self) -> None:
        ledger = AppliedOpLedger()
        ledger._persist = lambda: False  # type: ignore[method-assign]
        self.assertFalse(ledger.record("client", "op-1", "text"))
        self.assertEqual(ledger.unavailable_reason, "persist_failed")
        self.assertEqual(ledger.status("client", "op-2", "other"), "unavailable")

    def test_ledger_is_bounded(self) -> None:
        ledger = AppliedOpLedger(max_entries=2)
        ledger.record("client", "op-1", "a")
        ledger.record("client", "op-2", "b")
        ledger.record("client", "op-3", "c")
        self.assertFalse(ledger.contains("client", "op-1"))
        self.assertTrue(ledger.contains("client", "op-2"))
        self.assertTrue(ledger.contains("client", "op-3"))


if __name__ == "__main__":
    unittest.main()
