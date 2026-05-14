from __future__ import annotations

import ctypes
import unittest

from host.injector import INPUT, INPUTUNION, KEYBDINPUT


class InjectorLayoutTests(unittest.TestCase):
    def test_input_layout_has_union_keyboard_field(self) -> None:
        self.assertTrue(hasattr(INPUTUNION, "_fields_"))
        self.assertEqual(INPUT._fields_[1][0], "u")
        union_field_names = [name for name, _ in INPUTUNION._fields_]
        self.assertIn("ki", union_field_names)

    def test_keybdinput_has_integral_extra_info(self) -> None:
        field_names = [name for name, _ in KEYBDINPUT._fields_]
        self.assertIn("dwExtraInfo", field_names)
        extra_field_type = dict(KEYBDINPUT._fields_)["dwExtraInfo"]
        self.assertIn(ctypes.sizeof(extra_field_type), (4, 8))

    def test_input_size_matches_windows_expectation(self) -> None:
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)


if __name__ == "__main__":
    unittest.main()
