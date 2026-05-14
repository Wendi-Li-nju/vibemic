from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


DISALLOWED_CHARS = {"\n", "\r", "\t", "\b"}
SUPPORTED_PASTE_MODES = {"auto", "ctrl_v", "ctrl_shift_v", "shift_insert"}


class ProtocolError(ValueError):
    pass


def parse_message(payload: str) -> dict[str, Any]:
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProtocolError("invalid_json") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("invalid_message_shape")
    if "type" not in obj or not isinstance(obj["type"], str):
        raise ProtocolError("missing_type")
    return obj


@dataclass
class InsertMessage:
    session_id: str
    token: str
    seq: int
    text: str
    ts: int
    paste_mode: str | None


@dataclass
class ReplaceMessage:
    session_id: str
    token: str
    seq: int
    text: str
    ts: int


def normalize_paste_mode(value: Any, *, default: str = "auto") -> str:
    if value is None:
        return default
    if not isinstance(value, str) or value not in SUPPORTED_PASTE_MODES:
        raise ProtocolError("invalid_paste_mode")
    return value


def parse_insert(obj: dict[str, Any]) -> InsertMessage:
    for key in ("session_id", "token", "seq", "text", "ts"):
        if key not in obj:
            raise ProtocolError(f"missing_{key}")
    if not isinstance(obj["session_id"], str) or not obj["session_id"]:
        raise ProtocolError("invalid_session_id")
    if not isinstance(obj["token"], str) or not obj["token"]:
        raise ProtocolError("invalid_token")
    if not isinstance(obj["seq"], int) or obj["seq"] <= 0:
        raise ProtocolError("invalid_seq")
    if not isinstance(obj["text"], str) or len(obj["text"]) == 0:
        raise ProtocolError("invalid_text")
    if any(ch in DISALLOWED_CHARS for ch in obj["text"]):
        raise ProtocolError("unsupported_character")
    if not isinstance(obj["ts"], int) or obj["ts"] <= 0:
        raise ProtocolError("invalid_ts")
    return InsertMessage(
        session_id=obj["session_id"],
        token=obj["token"],
        seq=obj["seq"],
        text=obj["text"],
        ts=obj["ts"],
        paste_mode=normalize_paste_mode(obj.get("paste_mode"), default="auto") if "paste_mode" in obj else None,
    )


def parse_replace(obj: dict[str, Any]) -> ReplaceMessage:
    for key in ("session_id", "token", "seq", "text", "ts"):
        if key not in obj:
            raise ProtocolError(f"missing_{key}")
    if not isinstance(obj["session_id"], str) or not obj["session_id"]:
        raise ProtocolError("invalid_session_id")
    if not isinstance(obj["token"], str) or not obj["token"]:
        raise ProtocolError("invalid_token")
    if not isinstance(obj["seq"], int) or obj["seq"] <= 0:
        raise ProtocolError("invalid_seq")
    if not isinstance(obj["text"], str):
        raise ProtocolError("invalid_text")
    if any(ch in DISALLOWED_CHARS for ch in obj["text"]):
        raise ProtocolError("unsupported_character")
    if not isinstance(obj["ts"], int) or obj["ts"] <= 0:
        raise ProtocolError("invalid_ts")
    return ReplaceMessage(
        session_id=obj["session_id"],
        token=obj["token"],
        seq=obj["seq"],
        text=obj["text"],
        ts=obj["ts"],
    )
