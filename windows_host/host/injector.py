from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from typing import Optional, Protocol


WORD = ctypes.c_uint16
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32


class TextInjector(Protocol):
    def inject_text(self, text: str) -> None:
        ...

    def replace_text(self, text: str) -> None:
        ...

    def append_text(self, text: str) -> None:
        ...

    def set_paste_mode(self, mode: str) -> None:
        ...


@dataclass
class MockInjector:
    applied: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)

    def inject_text(self, text: str) -> None:
        self.applied.append(text)

    def replace_text(self, text: str) -> None:
        self.replaced.append(text)

    def append_text(self, text: str) -> None:
        self.applied.append(text)

    def set_paste_mode(self, mode: str) -> None:
        return None


if ctypes.sizeof(ctypes.c_void_p) == 8:
    ULONG_PTR = ctypes.c_uint64
else:
    ULONG_PTR = ctypes.c_uint32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", DWORD),
        ("wParamL", WORD),
        ("wParamH", WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", DWORD),
        ("u", INPUTUNION),
    ]


class FocusGuard:
    @staticmethod
    def ensure_focus_ready(user32: ctypes.WinDLL) -> None:
        foreground = user32.GetForegroundWindow()
        if foreground == 0:
            raise RuntimeError("focus_unavailable")
        if user32.IsWindowVisible(foreground) == 0:
            raise RuntimeError("focus_unavailable")
        if user32.IsIconic(foreground) != 0:
            raise RuntimeError("focus_unavailable")


class WindowsSendInputInjector:
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    INPUT_KEYBOARD = 1
    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_BACK = 0x08

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("WindowsSendInputInjector requires Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

    def _send_input(self, key_input: INPUT) -> None:
        ctypes.set_last_error(0)
        sent = self.user32.SendInput(1, ctypes.byref(key_input), ctypes.sizeof(INPUT))
        if sent != 1:
            last_error = ctypes.get_last_error()
            raise RuntimeError(f"sendinput_failed:{last_error}")

    def _send_unicode_char(self, char: str) -> None:
        code_unit = ord(char)
        key_down = INPUT(
            type=self.INPUT_KEYBOARD,
            u=INPUTUNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=code_unit,
                    dwFlags=self.KEYEVENTF_UNICODE,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        key_up = INPUT(
            type=self.INPUT_KEYBOARD,
            u=INPUTUNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=code_unit,
                    dwFlags=self.KEYEVENTF_UNICODE | self.KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        self._send_input(key_down)
        self._send_input(key_up)

    def _send_vk(self, vk: int, key_up: bool = False) -> None:
        flags = self.KEYEVENTF_KEYUP if key_up else 0
        key = INPUT(
            type=self.INPUT_KEYBOARD,
            u=INPUTUNION(
                ki=KEYBDINPUT(
                    wVk=vk,
                    wScan=0,
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        self._send_input(key)

    def inject_text(self, text: str) -> None:
        FocusGuard.ensure_focus_ready(self.user32)
        for ch in text:
            self._send_unicode_char(ch)

    def replace_text(self, text: str) -> None:
        FocusGuard.ensure_focus_ready(self.user32)
        self._send_vk(self.VK_CONTROL, key_up=False)
        self._send_vk(self.VK_A, key_up=False)
        self._send_vk(self.VK_A, key_up=True)
        self._send_vk(self.VK_CONTROL, key_up=True)
        self._send_vk(self.VK_BACK, key_up=False)
        self._send_vk(self.VK_BACK, key_up=True)
        for ch in text:
            self._send_unicode_char(ch)

    def append_text(self, text: str) -> None:
        self.inject_text(text)

    def set_paste_mode(self, mode: str) -> None:
        return None


class LinuxKeyBackend(Protocol):
    def ensure_focus_ready(self) -> None:
        ...

    def focused_window_info(self) -> dict[str, str]:
        ...

    def tap_key_name(self, name: str) -> None:
        ...

    def press_key_name(self, name: str) -> None:
        ...

    def release_key_name(self, name: str) -> None:
        ...

    def tap_unicode_char(self, char: str) -> None:
        ...


class ClipboardProvider(Protocol):
    def set_text(self, text: str) -> None:
        ...

    def get_text(self) -> Optional[str]:
        ...


class GtkClipboardProvider:
    def __init__(self) -> None:
        try:
            import gi  # type: ignore

            gi.require_version("Gtk", "3.0")
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk, GLib, Gtk  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on host environment
            raise RuntimeError("clipboard_unavailable") from exc

        self._Gdk = Gdk
        self._GLib = GLib
        self._Gtk = Gtk
        self._loop_ready = threading.Event()
        self._clipboard = None
        self._owner_change_event = threading.Event()
        self._thread = threading.Thread(target=self._run_mainloop, name="rtcs-gtk-clipboard", daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=3):
            raise RuntimeError("clipboard_unavailable")

    def _run_mainloop(self) -> None:
        self._Gtk.init_check()
        display = self._Gdk.Display.get_default()
        if display is None:
            return
        clipboard = self._Gtk.Clipboard.get_for_display(display, self._Gdk.SELECTION_CLIPBOARD)
        clipboard.connect("owner-change", self._on_owner_change)
        self._clipboard = clipboard
        self._loop = self._GLib.MainLoop()
        self._loop_ready.set()
        self._loop.run()

    def _on_owner_change(self, *_args: object) -> None:
        self._owner_change_event.set()

    def set_text(self, text: str) -> None:
        self._owner_change_event.clear()

        def _set() -> bool:
            if self._clipboard is None:
                raise RuntimeError("clipboard_unavailable")
            self._clipboard.set_text(text, -1)
            self._clipboard.store()
            return False

        self._GLib.idle_add(_set)
        if not self._owner_change_event.wait(timeout=1.5):
            raise RuntimeError("clipboard_unavailable")

    def get_text(self) -> Optional[str]:
        result: dict[str, Optional[str]] = {"value": None}
        done = threading.Event()

        def _get() -> bool:
            if self._clipboard is not None:
                result["value"] = self._clipboard.wait_for_text()
            done.set()
            return False

        self._GLib.idle_add(_get)
        if not done.wait(timeout=1.5):
            raise RuntimeError("clipboard_unavailable")
        return result["value"]


class AtspiTextBackend:
    EDITABLE_ROLE_NAMES = {
        "document text",
        "entry",
        "paragraph",
        "password text",
        "terminal",
        "text",
    }

    def __init__(self, pyatspi_module: Optional[object] = None) -> None:
        self.pyatspi = pyatspi_module or self._import_pyatspi()

    @staticmethod
    def _import_pyatspi() -> object:
        try:
            import pyatspi  # type: ignore
        except Exception as exc:  # pragma: no cover - import depends on host environment
            raise RuntimeError("backend_unavailable") from exc
        return pyatspi

    def append_text(self, text: str) -> None:
        if not text:
            return
        editable = self._focused_editable_text()
        if editable is None:
            raise RuntimeError("editable_target_unavailable")

        try:
            before_offset = int(editable.caretOffset)
            before_count = int(editable.characterCount)
            editable.insertText(before_offset, text, len(text))
        except Exception as exc:
            raise RuntimeError("insert_failed") from exc

        try:
            editable.setCaretOffset(before_offset + len(text))
        except Exception:
            pass

        if not self._verify_insert(editable, before_offset, before_count, text):
            raise RuntimeError("insert_unverified")

    def set_paste_mode(self, mode: str) -> None:
        return None

    def _verify_insert(self, editable: object, before_offset: int, before_count: int, text: str) -> bool:
        try:
            after_count = int(editable.characterCount)
            if after_count >= before_count + len(text):
                inserted = editable.getText(before_offset, before_offset + len(text))
                if inserted == text:
                    return True
        except Exception:
            pass

        try:
            return int(editable.caretOffset) >= before_offset + len(text)
        except Exception:
            return False

    def _focused_editable_text(self) -> Optional[object]:
        focused = self._find_focused_accessible()
        if focused is None:
            return None

        for candidate in self._candidate_accessibles(focused):
            editable = self._query_editable_text(candidate)
            if editable is not None:
                return editable
        return None

    def _candidate_accessibles(self, focused: object) -> list[object]:
        candidates: list[object] = []
        seen: set[int] = set()
        lineage = self._lineage(focused)

        for accessible in lineage:
            self._add_candidate(candidates, seen, accessible)

        for accessible in lineage:
            for descendant in self._descendant_candidates(accessible):
                self._add_candidate(candidates, seen, descendant)
        return candidates

    def _lineage(self, accessible: object) -> list[object]:
        lineage: list[object] = []
        current = accessible
        while current is not None:
            lineage.append(current)
            current = getattr(current, "parent", None)
        return lineage

    def _descendant_candidates(self, accessible: object) -> list[object]:
        try:
            descendants = list(self.pyatspi.findAllDescendants(accessible, self._is_editable_candidate))
        except Exception:
            return []
        descendants.sort(key=self._candidate_sort_key)
        return descendants

    def _candidate_sort_key(self, accessible: object) -> tuple[int, str]:
        try:
            state = accessible.getState()
            focused = bool(state.contains(self.pyatspi.STATE_FOCUSED))
            editable = bool(state.contains(self.pyatspi.STATE_EDITABLE))
        except Exception:
            focused = False
            editable = False
        role_name = self._role_name(accessible)
        return (
            0 if focused else 1,
            0 if editable else 1,
            0 if role_name in self.EDITABLE_ROLE_NAMES else 1,
            role_name,
        )

    def _query_editable_text(self, accessible: object) -> Optional[object]:
        try:
            return accessible.queryEditableText()
        except Exception:
            return None

    def _is_editable_candidate(self, accessible: object) -> bool:
        if self._query_editable_text(accessible) is not None:
            return True
        try:
            state = accessible.getState()
            if state.contains(self.pyatspi.STATE_EDITABLE):
                return True
        except Exception:
            pass
        return self._role_name(accessible) in self.EDITABLE_ROLE_NAMES

    @staticmethod
    def _add_candidate(candidates: list[object], seen: set[int], accessible: object) -> None:
        key = id(accessible)
        if key in seen:
            return
        seen.add(key)
        candidates.append(accessible)

    @staticmethod
    def _role_name(accessible: object) -> str:
        try:
            return str(accessible.getRoleName())
        except Exception:
            return ""

    def _find_focused_accessible(self) -> Optional[object]:
        try:
            desktop_count = int(self.pyatspi.Registry.getDesktopCount())
        except Exception as exc:
            raise RuntimeError("backend_unavailable") from exc

        focused_nodes: list[object] = []
        for index in range(desktop_count):
            desktop = self.pyatspi.Registry.getDesktop(index)
            if self._is_focused(desktop):
                focused_nodes.append(desktop)
            try:
                focused_nodes.extend(self.pyatspi.findAllDescendants(desktop, self._is_focused))
            except Exception:
                focused = self.pyatspi.findDescendant(desktop, self._is_focused, breadth_first=True)
                if focused is not None:
                    focused_nodes.append(focused)
        if not focused_nodes:
            return None
        focused_nodes.sort(key=self._focus_anchor_sort_key)
        return focused_nodes[0]

    def _focus_anchor_sort_key(self, accessible: object) -> tuple[int, int, int, str]:
        role_name = self._role_name(accessible)
        try:
            state = accessible.getState()
            editable = bool(state.contains(self.pyatspi.STATE_EDITABLE))
        except Exception:
            editable = False
        return (
            0 if editable else 1,
            0 if role_name in self.EDITABLE_ROLE_NAMES else 1,
            -self._depth(accessible),
            role_name,
        )

    def _is_focused(self, accessible: object) -> bool:
        try:
            return bool(accessible.getState().contains(self.pyatspi.STATE_FOCUSED))
        except Exception:
            return False

    @staticmethod
    def _depth(accessible: object) -> int:
        depth = 0
        current = getattr(accessible, "parent", None)
        while current is not None:
            depth += 1
            current = getattr(current, "parent", None)
        return depth


class X11CtypesBackend:
    NONE_FOCUS = 0
    POINTER_ROOT = 1
    CURRENT_TIME = 0
    ANY_PROPERTY_TYPE = 0

    def __init__(self, display_name: str | None = None) -> None:
        self.display_name = display_name or os.environ.get("DISPLAY", "")
        if not self.display_name:
            raise RuntimeError("x11_display_unavailable")
        self.key_event_delay_ms = max(0.0, float(os.environ.get("RTCS_X11_KEY_DELAY_MS", "1")))
        self.mapping_settle_delay_ms = max(0.0, float(os.environ.get("RTCS_X11_MAPPING_DELAY_MS", "0")))

        x11_name = ctypes.util.find_library("X11")
        xtst_name = ctypes.util.find_library("Xtst")
        if not x11_name or not xtst_name:
            raise RuntimeError("x11_libraries_unavailable")

        self._x11 = ctypes.CDLL(x11_name)
        self._xtst = ctypes.CDLL(xtst_name)
        self._configure_signatures()

        self._display = self._x11.XOpenDisplay(self.display_name.encode("utf-8"))
        if not self._display:
            raise RuntimeError("x11_display_open_failed")

        if not self._xtest_available():
            self.close()
            raise RuntimeError("xtest_unavailable")

        self._modifier_keycodes = self._load_modifier_keycodes()
        self._layout_char_map = self._build_layout_char_map()

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        display = getattr(self, "_display", None)
        if display:
            self._x11.XCloseDisplay(display)
            self._display = None

    def _configure_signatures(self) -> None:
        display_p = ctypes.c_void_p
        window_p = ctypes.POINTER(ctypes.c_ulong)
        int_p = ctypes.POINTER(ctypes.c_int)
        keysym_p = ctypes.POINTER(ctypes.c_ulong)

        self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self._x11.XOpenDisplay.restype = display_p
        self._x11.XCloseDisplay.argtypes = [display_p]
        self._x11.XCloseDisplay.restype = ctypes.c_int
        self._x11.XFlush.argtypes = [display_p]
        self._x11.XFlush.restype = ctypes.c_int
        self._x11.XSync.argtypes = [display_p, ctypes.c_int]
        self._x11.XSync.restype = ctypes.c_int
        self._x11.XGetInputFocus.argtypes = [display_p, window_p, int_p]
        self._x11.XGetInputFocus.restype = ctypes.c_int
        self._x11.XQueryTree.argtypes = [
            display_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
            ctypes.POINTER(ctypes.c_uint),
        ]
        self._x11.XQueryTree.restype = ctypes.c_int
        self._x11.XInternAtom.argtypes = [display_p, ctypes.c_char_p, ctypes.c_int]
        self._x11.XInternAtom.restype = ctypes.c_ulong
        self._x11.XGetWindowProperty.argtypes = [
            display_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        self._x11.XGetWindowProperty.restype = ctypes.c_int
        self._x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self._x11.XStringToKeysym.restype = ctypes.c_ulong
        self._x11.XKeysymToKeycode.argtypes = [display_p, ctypes.c_ulong]
        self._x11.XKeysymToKeycode.restype = ctypes.c_uint8
        self._x11.XDisplayKeycodes.argtypes = [display_p, int_p, int_p]
        self._x11.XDisplayKeycodes.restype = ctypes.c_int
        self._x11.XGetKeyboardMapping.argtypes = [display_p, ctypes.c_uint8, ctypes.c_int, int_p]
        self._x11.XGetKeyboardMapping.restype = keysym_p
        self._x11.XChangeKeyboardMapping.argtypes = [display_p, ctypes.c_int, ctypes.c_int, keysym_p, ctypes.c_int]
        self._x11.XChangeKeyboardMapping.restype = ctypes.c_int
        self._x11.XFree.argtypes = [ctypes.c_void_p]
        self._x11.XFree.restype = ctypes.c_int

        self._xtst.XTestFakeKeyEvent.argtypes = [display_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self._xtst.XTestFakeKeyEvent.restype = ctypes.c_int
        self._xtst.XTestQueryExtension.argtypes = [display_p, int_p, int_p, int_p, int_p]
        self._xtst.XTestQueryExtension.restype = ctypes.c_int

    def _xtest_available(self) -> bool:
        event_base = ctypes.c_int()
        error_base = ctypes.c_int()
        major = ctypes.c_int()
        minor = ctypes.c_int()
        return bool(
            self._xtst.XTestQueryExtension(
                self._display,
                ctypes.byref(event_base),
                ctypes.byref(error_base),
                ctypes.byref(major),
                ctypes.byref(minor),
            )
        )

    def ensure_focus_ready(self) -> None:
        focus_window = ctypes.c_ulong()
        revert_to = ctypes.c_int()
        self._x11.XGetInputFocus(self._display, ctypes.byref(focus_window), ctypes.byref(revert_to))
        if focus_window.value in (self.NONE_FOCUS, self.POINTER_ROOT):
            raise RuntimeError("focus_unavailable")

    def focused_window_info(self) -> dict[str, str]:
        focus_window = ctypes.c_ulong()
        revert_to = ctypes.c_int()
        self._x11.XGetInputFocus(self._display, ctypes.byref(focus_window), ctypes.byref(revert_to))
        if focus_window.value in (self.NONE_FOCUS, self.POINTER_ROOT):
            return {"wm_class": "", "wm_name": ""}
        window = self._resolve_app_window(focus_window.value)
        return {
            "wm_class": self._get_text_property(window, "WM_CLASS").lower(),
            "wm_name": self._get_text_property(window, "_NET_WM_NAME") or self._get_text_property(window, "WM_NAME"),
        }

    def _resolve_app_window(self, window: int) -> int:
        current = window
        last_with_class = current
        for _ in range(8):
            wm_class = self._get_text_property(current, "WM_CLASS")
            if wm_class:
                last_with_class = current
            parent = self._query_parent_window(current)
            if parent == 0 or parent == current:
                break
            current = parent
        return last_with_class

    def _query_parent_window(self, window: int) -> int:
        root = ctypes.c_ulong()
        parent = ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        child_count = ctypes.c_uint()
        status = self._x11.XQueryTree(
            self._display,
            window,
            ctypes.byref(root),
            ctypes.byref(parent),
            ctypes.byref(children),
            ctypes.byref(child_count),
        )
        if children:
            self._x11.XFree(children)
        if status == 0:
            return 0
        return int(parent.value)

    def _get_text_property(self, window: int, property_name: str) -> str:
        atom = self._x11.XInternAtom(self._display, property_name.encode("ascii"), 1)
        if atom == 0:
            return ""

        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        nitems = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        prop = ctypes.POINTER(ctypes.c_ubyte)()

        status = self._x11.XGetWindowProperty(
            self._display,
            window,
            atom,
            0,
            1024,
            0,
            self.ANY_PROPERTY_TYPE,
            ctypes.byref(actual_type),
            ctypes.byref(actual_format),
            ctypes.byref(nitems),
            ctypes.byref(bytes_after),
            ctypes.byref(prop),
        )
        if status != 0 or not prop:
            return ""
        try:
            raw = ctypes.string_at(prop, int(nitems.value) * max(actual_format.value // 8, 1))
        finally:
            self._x11.XFree(prop)
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()

    def press_key_name(self, name: str) -> None:
        self._fake_key_event(self._keycode_for_name(name), True)

    def release_key_name(self, name: str) -> None:
        self._fake_key_event(self._keycode_for_name(name), False)

    def tap_key_name(self, name: str) -> None:
        keycode = self._keycode_for_name(name)
        self._fake_key_event(keycode, True)
        self._fake_key_event(keycode, False)

    def tap_unicode_char(self, char: str) -> None:
        mapping = self._layout_char_map.get(char)
        if mapping is None:
            raise RuntimeError("unsupported_character_on_current_layout")
        keycode, modifiers = mapping
        for modifier in modifiers:
            self.press_key_name(modifier)
        try:
            self._fake_key_event(keycode, True)
            self._fake_key_event(keycode, False)
        finally:
            for modifier in reversed(modifiers):
                self.release_key_name(modifier)

    @staticmethod
    def _char_to_keysym(char: str) -> int:
        codepoint = ord(char)
        if codepoint <= 0xFF:
            return codepoint
        return 0x01000000 | codepoint

    def _keycode_for_name(self, name: str) -> int:
        keysym = self._x11.XStringToKeysym(name.encode("ascii"))
        if keysym == 0:
            raise RuntimeError(f"unknown_keysym:{name}")
        keycode = int(self._x11.XKeysymToKeycode(self._display, keysym))
        if keycode == 0:
            raise RuntimeError(f"unmapped_keysym:{name}")
        return keycode

    def _load_modifier_keycodes(self) -> dict[str, int]:
        modifier_keycodes: dict[str, int] = {}
        modifier_keycodes["shift"] = self._keycode_for_name("Shift_L")
        for name in ("ISO_Level3_Shift", "Mode_switch"):
            try:
                modifier_keycodes["altgr"] = self._keycode_for_name(name)
                break
            except RuntimeError:
                continue
        return modifier_keycodes

    def _build_layout_char_map(self) -> dict[str, tuple[int, tuple[str, ...]]]:
        min_keycode = ctypes.c_int()
        max_keycode = ctypes.c_int()
        self._x11.XDisplayKeycodes(self._display, ctypes.byref(min_keycode), ctypes.byref(max_keycode))
        keycode_count = max_keycode.value - min_keycode.value + 1
        keysyms_per_keycode = ctypes.c_int()
        mapping_ptr = self._x11.XGetKeyboardMapping(
            self._display,
            min_keycode.value,
            keycode_count,
            ctypes.byref(keysyms_per_keycode),
        )
        if not mapping_ptr:
            raise RuntimeError("keyboard_mapping_unavailable")

        try:
            char_map: dict[str, tuple[int, tuple[str, ...]]] = {}
            for index in range(keycode_count):
                offset = index * keysyms_per_keycode.value
                keycode = min_keycode.value + index
                for slot in range(min(4, keysyms_per_keycode.value)):
                    char = self._keysym_to_char(int(mapping_ptr[offset + slot]))
                    modifiers = self._modifiers_for_slot(slot)
                    if char is None or not self._modifiers_available(modifiers):
                        continue
                    existing = char_map.get(char)
                    if existing is None or len(modifiers) < len(existing[1]):
                        char_map[char] = (keycode, modifiers)
        finally:
            self._x11.XFree(mapping_ptr)

        return char_map

    @staticmethod
    def _keysym_to_char(keysym: int) -> Optional[str]:
        if keysym == 0:
            return None
        if 0x20 <= keysym <= 0xFF and not (0x7F <= keysym <= 0x9F):
            char = chr(keysym)
            return char if char.isprintable() else None
        if keysym & 0xFF000000 == 0x01000000:
            codepoint = keysym & 0x00FFFFFF
            try:
                char = chr(codepoint)
            except ValueError:
                return None
            return char if char.isprintable() else None
        return None

    def _modifiers_available(self, modifiers: tuple[str, ...]) -> bool:
        return all(name in self._modifier_keycodes for name in modifiers)

    @staticmethod
    def _modifiers_for_slot(slot: int) -> tuple[str, ...]:
        if slot == 0:
            return ()
        if slot == 1:
            return ("shift",)
        if slot == 2:
            return ("altgr",)
        if slot == 3:
            return ("shift", "altgr")
        return ()

    def _fake_key_event(self, keycode: int, pressed: bool) -> None:
        status = self._xtst.XTestFakeKeyEvent(self._display, keycode, int(pressed), self.CURRENT_TIME)
        if status == 0:
            raise RuntimeError("xtest_fake_key_failed")
        self._sync()
        self._sleep_ms(self.key_event_delay_ms)

    def _sync(self) -> None:
        self._x11.XFlush(self._display)
        self._x11.XSync(self._display, 0)

    @staticmethod
    def _sleep_ms(delay_ms: float) -> None:
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)


class LinuxX11Injector:
    def __init__(
        self,
        backend: LinuxKeyBackend | None = None,
    ) -> None:
        if platform.system() != "Linux":
            raise RuntimeError("LinuxX11Injector requires Linux")
        self.backend = backend or X11CtypesBackend()

    def inject_text(self, text: str) -> None:
        self.backend.ensure_focus_ready()
        for ch in text:
            self.backend.tap_unicode_char(ch)

    def replace_text(self, text: str) -> None:
        self.backend.ensure_focus_ready()
        self.backend.press_key_name("Control_L")
        try:
            self.backend.tap_key_name("a")
        finally:
            self.backend.release_key_name("Control_L")
        self.backend.tap_key_name("BackSpace")
        for ch in text:
            self.backend.tap_unicode_char(ch)

    def append_text(self, text: str) -> None:
        if not text:
            return
        self.inject_text(text)

    def set_paste_mode(self, mode: str) -> None:
        return None


class LinuxClipboardPasteInjector:
    def __init__(
        self,
        backend: LinuxKeyBackend | None = None,
        clipboard: ClipboardProvider | None = None,
        paste_mode: str = "ctrl_v",
    ) -> None:
        if platform.system() != "Linux":
            raise RuntimeError("LinuxClipboardPasteInjector requires Linux")
        self.backend = backend or X11CtypesBackend()
        self.clipboard = clipboard or GtkClipboardProvider()
        self.paste_mode = paste_mode

    def set_paste_mode(self, mode: str) -> None:
        self.paste_mode = mode

    def inject_text(self, text: str) -> None:
        self.append_text(text)

    def replace_text(self, text: str) -> None:
        self.append_text(text)

    def append_text(self, text: str) -> None:
        if not text:
            return
        self.backend.ensure_focus_ready()
        try:
            self.clipboard.set_text(text)
        except RuntimeError:
            raise
        self._send_paste_shortcut(self._resolve_paste_mode())

    def _resolve_paste_mode(self) -> str:
        if self.paste_mode != "auto":
            return self.paste_mode
        info = self.backend.focused_window_info()
        wm_class = info.get("wm_class", "")
        wm_name = info.get("wm_name", "").lower()
        terminal_markers = (
            "warp",
            "gnome-terminal",
            "kgx",
            "ptyxis",
            "konsole",
            "xterm",
            "alacritty",
            "kitty",
            "wezterm",
            "tilix",
        )
        if any(marker in wm_class for marker in terminal_markers) or any(marker in wm_name for marker in terminal_markers):
            return "ctrl_shift_v"
        return "ctrl_v"

    def _send_paste_shortcut(self, mode: str) -> None:
        if mode == "ctrl_v":
            self.backend.press_key_name("Control_L")
            try:
                self.backend.tap_key_name("v")
            finally:
                self.backend.release_key_name("Control_L")
            return
        if mode == "ctrl_shift_v":
            self.backend.press_key_name("Control_L")
            self.backend.press_key_name("Shift_L")
            try:
                self.backend.tap_key_name("v")
            finally:
                self.backend.release_key_name("Shift_L")
                self.backend.release_key_name("Control_L")
            return
        if mode == "shift_insert":
            self.backend.press_key_name("Shift_L")
            try:
                self.backend.tap_key_name("Insert")
            finally:
                self.backend.release_key_name("Shift_L")
            return
        raise RuntimeError("unsupported_paste_mode")


class LinuxTextInjector:
    def __init__(
        self,
        primary: Optional[LinuxClipboardPasteInjector] = None,
        atspi: Optional[AtspiTextBackend] = None,
        fallback: Optional[LinuxX11Injector] = None,
    ) -> None:
        if platform.system() != "Linux":
            raise RuntimeError("LinuxTextInjector requires Linux")
        self.primary = primary
        self.atspi = atspi
        self.fallback = fallback or LinuxX11Injector()

    def set_paste_mode(self, mode: str) -> None:
        if self.primary is not None:
            self.primary.set_paste_mode(mode)

    def inject_text(self, text: str) -> None:
        self.append_text(text)

    def replace_text(self, text: str) -> None:
        self.fallback.replace_text(text)

    def append_text(self, text: str) -> None:
        if not text:
            return
        if self.primary is not None:
            try:
                self.primary.append_text(text)
                return
            except RuntimeError as exc:
                if str(exc) not in {"backend_unavailable", "clipboard_unavailable", "focus_unavailable", "unsupported_paste_mode"}:
                    raise
        if self.atspi is not None:
            try:
                self.atspi.append_text(text)
                return
            except RuntimeError as exc:
                if str(exc) not in {"backend_unavailable", "editable_target_unavailable", "insert_failed", "insert_unverified"}:
                    raise
        self.fallback.append_text(text)

def create_default_injector() -> TextInjector:
    if platform.system() == "Windows":
        return WindowsSendInputInjector()
    if platform.system() == "Linux" and os.environ.get("DISPLAY"):
        try:
            primary: Optional[LinuxClipboardPasteInjector] = None
            atspi: Optional[AtspiTextBackend] = None
            try:
                primary = LinuxClipboardPasteInjector()
            except RuntimeError:
                primary = None
            try:
                atspi = AtspiTextBackend()
            except RuntimeError:
                atspi = None
            return LinuxTextInjector(primary=primary, atspi=atspi, fallback=LinuxX11Injector())
        except RuntimeError:
            return MockInjector()
    return MockInjector()
