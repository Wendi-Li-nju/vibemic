from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from host.identity import load_or_create_server_id


class ServerIdentityTests(unittest.TestCase):
    def test_persistent_server_id_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_id"
            first = load_or_create_server_id(path)
            second = load_or_create_server_id(path)
            self.assertEqual(first, second)
            self.assertEqual(str(uuid.UUID(first)), first)

    def test_ephemeral_identity_is_valid_uuid(self) -> None:
        value = load_or_create_server_id(None)
        self.assertEqual(str(uuid.UUID(value)), value)


if __name__ == "__main__":
    unittest.main()
