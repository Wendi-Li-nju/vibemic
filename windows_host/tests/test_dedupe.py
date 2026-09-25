from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from host.dedupe import AppliedOpLedger


class AppliedOpLedgerTests(unittest.TestCase):
    def test_memory_ledger_dedupes(self) -> None:
        ledger = AppliedOpLedger()
        self.assertFalse(ledger.contains("client", "op-1"))
        self.assertTrue(ledger.record("client", "op-1"))
        self.assertTrue(ledger.contains("client", "op-1"))
        self.assertFalse(ledger.contains("other-client", "op-1"))

    def test_persistent_ledger_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "applied.json"
            first = AppliedOpLedger(path=path)
            self.assertTrue(first.record("client", "op-1"))

            second = AppliedOpLedger(path=path)
            self.assertTrue(second.contains("client", "op-1"))

    def test_ledger_is_bounded(self) -> None:
        ledger = AppliedOpLedger(max_entries=2)
        ledger.record("client", "op-1")
        ledger.record("client", "op-2")
        ledger.record("client", "op-3")
        self.assertFalse(ledger.contains("client", "op-1"))
        self.assertTrue(ledger.contains("client", "op-2"))
        self.assertTrue(ledger.contains("client", "op-3"))


if __name__ == "__main__":
    unittest.main()
