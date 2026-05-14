from __future__ import annotations

import unittest

from host.protocol import ProtocolError, parse_insert, parse_message, parse_replace


class ProtocolTests(unittest.TestCase):
    def test_parse_message_requires_type(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_message("{}")

    def test_parse_insert_valid(self) -> None:
        msg = parse_insert(
            {
                "session_id": "s1",
                "token": "t1",
                "seq": 1,
                "text": "abc",
                "ts": 123456,
            }
        )
        self.assertEqual(msg.seq, 1)
        self.assertEqual(msg.text, "abc")

    def test_parse_insert_rejects_control_chars(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_insert(
                {
                    "session_id": "s1",
                    "token": "t1",
                    "seq": 1,
                    "text": "a\n",
                    "ts": 123456,
                }
            )

    def test_parse_insert_accepts_unicode_text(self) -> None:
        msg = parse_insert(
            {
                "session_id": "s1",
                "token": "t1",
                "seq": 1,
                "text": "你好abc123",
                "ts": 123456,
            }
        )
        self.assertEqual(msg.text, "你好abc123")

    def test_parse_replace_accepts_empty_text(self) -> None:
        msg = parse_replace(
            {
                "session_id": "s1",
                "token": "t1",
                "seq": 2,
                "text": "",
                "ts": 123457,
            }
        )
        self.assertEqual(msg.text, "")


if __name__ == "__main__":
    unittest.main()
