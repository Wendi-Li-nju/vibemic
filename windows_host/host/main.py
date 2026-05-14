from __future__ import annotations

import argparse
import logging

from aiohttp import web

from .config import HostConfig
from .server import build_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime cursor sync host")
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
    parser.add_argument(
        "--replace-quiet-window-ms",
        type=int,
        default=200,
        help="How long to wait after the latest snapshot before applying it",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


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
        replace_quiet_window_ms=args.replace_quiet_window_ms,
    )
    app = build_app(config=config)
    host = app["host"]
    logging.getLogger("realtime_cursor_sync.host").info(
        "Host listening at ws://%s:%s/ws",
        config.bind,
        config.port,
    )
    web.run_app(app, host=config.bind, port=config.port)


if __name__ == "__main__":
    run()
