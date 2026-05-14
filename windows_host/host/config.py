from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HostConfig:
    bind: str = "0.0.0.0"
    port: int = 8765
    heartbeat_interval_ms: int = 5000
    session_timeout_ms: int = 15000
    replace_quiet_window_ms: int = 200
