from __future__ import annotations

import unittest
from unittest import mock

from host.injector import (
    AtspiTextBackend,
    LinuxClipboardPasteInjector,
    LinuxTextInjector,
    LinuxX11Injector,
    MockInjector,
    create_default_injector,
)


class FakeLinuxBackend:
    def __init__(self, *, wm_class: str = "", wm_name: str = "") -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.wm_class = wm_class
        self.wm_name = wm_name

    def ensure_focus_ready(self) -> None:
        self.calls.append(("focus", None))

    def focused_window_info(self) -> dict[str, str]:
        return {"wm_class": self.wm_class, "wm_name": self.wm_name}

    def tap_key_name(self, name: str) -> None:
        self.calls.append(("tap_key", name))

    def press_key_name(self, name: str) -> None:
        self.calls.append(("press_key", name))

    def release_key_name(self, name: str) -> None:
        self.calls.append(("release_key", name))

    def tap_unicode_char(self, char: str) -> None:
        self.calls.append(("tap_char", char))


class FakeEditableText:
    def __init__(self, text: str = "", caret_offset: int = 0) -> None:
        self.buffer = text
        self.caretOffset = caret_offset
        self.characterCount = len(text)

    def insertText(self, position: int, text: str, _length: int) -> None:
        self.buffer = self.buffer[:position] + text + self.buffer[position:]
        self.characterCount = len(self.buffer)
        self.caretOffset = position + len(text)

    def setCaretOffset(self, offset: int) -> None:
        self.caretOffset = offset

    def getText(self, start_offset: int, end_offset: int) -> str:
        return self.buffer[start_offset:end_offset]


class FakeStateSet:
    def __init__(self, focused: bool) -> None:
        self.focused = focused

    def contains(self, _state: object) -> bool:
        return self.focused


class FakeAccessible:
    def __init__(
        self,
        editable_text: FakeEditableText | None = None,
        *,
        focused: bool = False,
        parent: "FakeAccessible | None" = None,
        role_name: str = "text",
        name: str = "",
        editable_state: bool = False,
        children: list["FakeAccessible"] | None = None,
    ) -> None:
        self._editable_text = editable_text
        self._focused = focused
        self.parent = parent
        self._role_name = role_name
        self.name = name
        self._editable_state = editable_state
        self.children = children or []
        for child in self.children:
            child.parent = self

    def getState(self) -> FakeStateSet:
        class _CombinedStateSet(FakeStateSet):
            def __init__(self, focused: bool, editable: bool) -> None:
                super().__init__(focused)
                self.editable = editable

            def contains(self, state: object) -> bool:
                if state == "editable-state":
                    return self.editable
                return super().contains(state)

        return _CombinedStateSet(self._focused, self._editable_state)

    def queryEditableText(self) -> FakeEditableText:
        if self._editable_text is None:
            raise RuntimeError("no_editable_text")
        return self._editable_text

    def getRoleName(self) -> str:
        return self._role_name


class FakeRegistry:
    def __init__(self, desktop: FakeAccessible | None) -> None:
        self._desktop = desktop

    def getDesktopCount(self) -> int:
        return 1

    def getDesktop(self, _index: int) -> object:
        return self._desktop or object()


class FakePyatspi:
    STATE_FOCUSED = object()
    STATE_EDITABLE = "editable-state"

    def __init__(self, root_accessible: FakeAccessible | None) -> None:
        self.Registry = FakeRegistry(root_accessible)
        self._root_accessible = root_accessible

    def findDescendant(self, _acc: object, pred, breadth_first: bool = False):  # noqa: ANN001
        descendants = self.findAllDescendants(_acc, pred)
        return descendants[0] if descendants else None

    def findAllDescendants(self, acc: object, pred):  # noqa: ANN001
        found: list[FakeAccessible] = []

        def walk(node: FakeAccessible) -> None:
            for child in node.children:
                if pred(child):
                    found.append(child)
                walk(child)

        if isinstance(acc, FakeAccessible):
            if pred(acc):
                found.append(acc)
            walk(acc)
        return found


class FakeAtspiBackend:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.appended: list[str] = []

    def append_text(self, text: str) -> None:
        if self.error is not None:
            raise RuntimeError(self.error)
        self.appended.append(text)


class FakeClipboard:
    def __init__(self) -> None:
        self.values: list[str] = []

    def set_text(self, text: str) -> None:
        self.values.append(text)

    def get_text(self) -> str | None:
        return self.values[-1] if self.values else None


class LinuxInjectorTests(unittest.TestCase):
    def test_inject_text_taps_each_character(self) -> None:
        backend = FakeLinuxBackend()
        injector = LinuxX11Injector(backend=backend)

        injector.inject_text("ab中")

        self.assertEqual(
            backend.calls,
            [
                ("focus", None),
                ("tap_char", "a"),
                ("tap_char", "b"),
                ("tap_char", "中"),
            ],
        )

    def test_replace_text_selects_all_then_types(self) -> None:
        backend = FakeLinuxBackend()
        injector = LinuxX11Injector(backend=backend)

        injector.replace_text("xy")

        self.assertEqual(
            backend.calls,
            [
                ("focus", None),
                ("press_key", "Control_L"),
                ("tap_key", "a"),
                ("release_key", "Control_L"),
                ("tap_key", "BackSpace"),
                ("tap_char", "x"),
                ("tap_char", "y"),
            ],
        )

    def test_append_text_directly_types_at_cursor(self) -> None:
        backend = FakeLinuxBackend()
        injector = LinuxX11Injector(backend=backend)

        injector.append_text("hello")

        self.assertEqual(
            backend.calls,
            [
                ("focus", None),
                ("tap_char", "h"),
                ("tap_char", "e"),
                ("tap_char", "l"),
                ("tap_char", "l"),
                ("tap_char", "o"),
            ],
        )

    def test_linux_clipboard_paste_injector_sets_clipboard_and_pastes(self) -> None:
        backend = FakeLinuxBackend()
        clipboard = FakeClipboard()
        injector = LinuxClipboardPasteInjector(backend=backend, clipboard=clipboard)

        injector.append_text("hello")

        self.assertEqual(clipboard.values, ["hello"])
        self.assertEqual(
            backend.calls,
            [
                ("focus", None),
                ("press_key", "Control_L"),
                ("tap_key", "v"),
                ("release_key", "Control_L"),
            ],
        )

    def test_linux_clipboard_paste_injector_uses_ctrl_shift_v_for_warp(self) -> None:
        backend = FakeLinuxBackend(wm_class="dev.warp.Warp")
        clipboard = FakeClipboard()
        injector = LinuxClipboardPasteInjector(backend=backend, clipboard=clipboard, paste_mode="auto")

        injector.append_text("hello")

        self.assertEqual(clipboard.values, ["hello"])
        self.assertEqual(
            backend.calls,
            [
                ("focus", None),
                ("press_key", "Control_L"),
                ("press_key", "Shift_L"),
                ("tap_key", "v"),
                ("release_key", "Shift_L"),
                ("release_key", "Control_L"),
            ],
        )

    def test_linux_clipboard_paste_injector_respects_explicit_paste_mode(self) -> None:
        backend = FakeLinuxBackend(wm_class="dev.warp.Warp")
        clipboard = FakeClipboard()
        injector = LinuxClipboardPasteInjector(backend=backend, clipboard=clipboard, paste_mode="ctrl_v")

        injector.append_text("hello")

        self.assertEqual(clipboard.values, ["hello"])
        self.assertEqual(
            backend.calls,
            [
                ("focus", None),
                ("press_key", "Control_L"),
                ("tap_key", "v"),
                ("release_key", "Control_L"),
            ],
        )

    def test_linux_text_injector_prefers_clipboard_append(self) -> None:
        primary = mock.create_autospec(LinuxClipboardPasteInjector, instance=True)
        atspi = FakeAtspiBackend()
        fallback = mock.create_autospec(LinuxX11Injector, instance=True)
        injector = LinuxTextInjector(primary=primary, atspi=atspi, fallback=fallback)

        injector.append_text("hello")

        primary.append_text.assert_called_once_with("hello")
        self.assertEqual(atspi.appended, [])
        fallback.append_text.assert_not_called()

    def test_linux_text_injector_forwards_selected_paste_mode(self) -> None:
        primary = mock.create_autospec(LinuxClipboardPasteInjector, instance=True)
        fallback = mock.create_autospec(LinuxX11Injector, instance=True)
        injector = LinuxTextInjector(primary=primary, atspi=None, fallback=fallback)

        injector.set_paste_mode("ctrl_shift_v")

        primary.set_paste_mode.assert_called_once_with("ctrl_shift_v")

    def test_linux_text_injector_falls_back_to_atspi_when_clipboard_unavailable(self) -> None:
        primary = mock.create_autospec(LinuxClipboardPasteInjector, instance=True)
        primary.append_text.side_effect = RuntimeError("clipboard_unavailable")
        atspi = FakeAtspiBackend()
        fallback = mock.create_autospec(LinuxX11Injector, instance=True)
        injector = LinuxTextInjector(primary=primary, atspi=atspi, fallback=fallback)

        injector.append_text("hello")

        self.assertEqual(atspi.appended, ["hello"])
        fallback.append_text.assert_not_called()

    def test_linux_text_injector_falls_back_to_x11_when_atspi_target_unavailable(self) -> None:
        primary = FakeAtspiBackend(error="editable_target_unavailable")
        clipboard = mock.create_autospec(LinuxClipboardPasteInjector, instance=True)
        clipboard.append_text.side_effect = RuntimeError("clipboard_unavailable")
        fallback = mock.create_autospec(LinuxX11Injector, instance=True)
        injector = LinuxTextInjector(primary=clipboard, atspi=primary, fallback=fallback)

        injector.append_text("hello")

        self.assertEqual(primary.appended, [])
        fallback.append_text.assert_called_once_with("hello")

    def test_create_default_injector_prefers_linux_text_stack(self) -> None:
        with mock.patch("host.injector.platform.system", return_value="Linux"):
            with mock.patch.dict("host.injector.os.environ", {"DISPLAY": ":1"}, clear=True):
                sentinel = object()
                with mock.patch("host.injector.LinuxX11Injector", return_value=sentinel):
                    with mock.patch("host.injector.LinuxClipboardPasteInjector", return_value=object()):
                        with mock.patch("host.injector.AtspiTextBackend", return_value=object()):
                            with mock.patch("host.injector.LinuxTextInjector", return_value=sentinel):
                                self.assertIs(create_default_injector(), sentinel)

    def test_create_default_injector_uses_mock_when_all_linux_backends_fail(self) -> None:
        with mock.patch("host.injector.platform.system", return_value="Linux"):
            with mock.patch.dict("host.injector.os.environ", {"DISPLAY": ":1"}, clear=True):
                with mock.patch("host.injector.LinuxClipboardPasteInjector", side_effect=RuntimeError("clipboard_unavailable")):
                    with mock.patch("host.injector.AtspiTextBackend", side_effect=RuntimeError("backend_unavailable")):
                        with mock.patch("host.injector.LinuxX11Injector", side_effect=RuntimeError("x11_failed")):
                            self.assertIsInstance(create_default_injector(), MockInjector)


class AtspiBackendTests(unittest.TestCase):
    def test_append_text_inserts_at_caret(self) -> None:
        editable = FakeEditableText(text="ab", caret_offset=2)
        focused = FakeAccessible(editable, focused=True)
        backend = AtspiTextBackend(pyatspi_module=FakePyatspi(focused))

        backend.append_text("中c")

        self.assertEqual(editable.buffer, "ab中c")
        self.assertEqual(editable.caretOffset, 4)

    def test_append_text_raises_when_no_editable_target(self) -> None:
        focused = FakeAccessible(None, focused=True)
        backend = AtspiTextBackend(pyatspi_module=FakePyatspi(focused))

        with self.assertRaisesRegex(RuntimeError, "editable_target_unavailable"):
            backend.append_text("hello")

    def test_append_text_finds_editable_descendant_under_focused_window(self) -> None:
        editable = FakeEditableText(text="", caret_offset=0)
        entry = FakeAccessible(editable, role_name="entry", editable_state=True)
        focused_window = FakeAccessible(
            None,
            focused=True,
            role_name="window",
            children=[entry],
        )
        backend = AtspiTextBackend(pyatspi_module=FakePyatspi(focused_window))

        backend.append_text("hello")

        self.assertEqual(editable.buffer, "hello")

    def test_append_text_prefers_deeper_focused_editable_over_focused_window(self) -> None:
        editable = FakeEditableText(text="", caret_offset=0)
        entry = FakeAccessible(
            editable,
            focused=True,
            role_name="entry",
            editable_state=True,
        )
        focused_window = FakeAccessible(
            None,
            focused=True,
            role_name="window",
            children=[entry],
        )
        backend = AtspiTextBackend(pyatspi_module=FakePyatspi(focused_window))

        backend.append_text("hello")

        self.assertEqual(editable.buffer, "hello")


class X11BackendHelperTests(unittest.TestCase):
    def test_char_to_keysym_encodes_non_latin_codepoints(self) -> None:
        from host.injector import X11CtypesBackend

        self.assertEqual(X11CtypesBackend._char_to_keysym("A"), ord("A"))
        self.assertEqual(X11CtypesBackend._char_to_keysym("中"), 0x01000000 | ord("中"))


if __name__ == "__main__":
    unittest.main()
