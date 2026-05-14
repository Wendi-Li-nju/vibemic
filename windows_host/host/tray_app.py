from __future__ import annotations

import argparse
import ctypes
import logging
import socket
from typing import Optional

from .config import HostConfig
from .service import HostService


def _show_message(title: str, message: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, message, title, 0)


def _detect_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime cursor sync host tray app")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8765, help="Bind port")
    parser.add_argument(
        "--heartbeat-interval-ms",
        type=int,
        default=5000,
        help="Heartbeat interval in milliseconds",
    )
    parser.add_argument(
        "--session-timeout-ms",
        type=int,
        default=15000,
        help="Session timeout in milliseconds",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


class TrayHostApp:
    def __init__(self, config: HostConfig) -> None:
        self.config = config
        self.service = HostService(config)
        self._icon = None
        self._lan_ip = _detect_lan_ip()

    def _status_text(self) -> str:
        running = "running" if self.service.is_running else "stopped"
        return f"Status: {running}\nEndpoint: ws://{self._lan_ip}:{self.config.port}/ws"

    def _start(self, icon, _item) -> None:
        if self.service.is_running:
            return
        try:
            self.service.start()
            _show_message("Realtime Cursor Host", self._status_text())
        except Exception as exc:  # pragma: no cover - runtime guard
            _show_message("Realtime Cursor Host", f"Failed to start service:\n{exc}")
        finally:
            icon.update_menu()

    def _stop(self, icon, _item) -> None:
        self.service.stop()
        icon.update_menu()

    def _show_status(self, _icon, _item) -> None:
        _show_message("Realtime Cursor Host", self._status_text())

    def _quit(self, icon, _item) -> None:
        self.service.stop()
        icon.stop()

    def run(self) -> None:
        import pystray
        from PIL import Image, ImageDraw

        def _create_image() -> Image.Image:
            image = Image.new("RGB", (64, 64), (26, 59, 91))
            draw = ImageDraw.Draw(image)
            draw.rectangle((8, 8, 56, 56), outline=(255, 255, 255), width=3)
            draw.line((20, 32, 44, 32), fill=(255, 255, 255), width=3)
            return image

        menu = pystray.Menu(
            pystray.MenuItem("Start Service", self._start, enabled=lambda _: not self.service.is_running),
            pystray.MenuItem("Stop Service", self._stop, enabled=lambda _: self.service.is_running),
            pystray.MenuItem("Show Status", self._show_status),
            pystray.MenuItem("Quit", self._quit),
        )
        self._icon = pystray.Icon("realtime_cursor_sync", _create_image(), "Realtime Cursor Host", menu)
        self._start(self._icon, None)
        self._icon.run()


def run() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = HostConfig(
        bind=args.bind,
        port=args.port,
        heartbeat_interval_ms=args.heartbeat_interval_ms,
        session_timeout_ms=args.session_timeout_ms,
    )
    app = TrayHostApp(config)
    app.run()
