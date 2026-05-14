from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Session:
    session_id: str
    token: str
    client_id: str
    paste_mode: str
    last_seq: int
    last_seen_ms: int
    applied_snapshot: str


class SessionManager:
    def __init__(self, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms
        self.active: Optional[Session] = None

    def authenticate(self, client_id: str, paste_mode: str = "auto") -> Session:
        session = Session(
            session_id=str(uuid.uuid4()),
            token=secrets.token_urlsafe(24),
            client_id=client_id,
            paste_mode=paste_mode,
            last_seq=0,
            last_seen_ms=now_ms(),
            applied_snapshot="",
        )
        self.active = session
        return session

    def validate(self, session_id: str, token: str) -> Session:
        if self.active is None:
            raise PermissionError("no_active_session")
        if self.active.session_id != session_id or self.active.token != token:
            raise PermissionError("invalid_session")
        return self.active

    def update_heartbeat(self, session_id: str, token: str) -> int:
        session = self.validate(session_id, token)
        session.last_seen_ms = now_ms()
        return session.last_seen_ms

    def check_timeout(self) -> bool:
        if self.active is None:
            return False
        if now_ms() - self.active.last_seen_ms > self.timeout_ms:
            self.active = None
            return True
        return False

    def expect_next_seq(self, session_id: str, token: str, seq: int) -> Session:
        session = self.validate(session_id, token)
        expected = session.last_seq + 1
        if seq != expected:
            raise ValueError("out_of_order")
        session.last_seq = seq
        session.last_seen_ms = now_ms()
        return session
