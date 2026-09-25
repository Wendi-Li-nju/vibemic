from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Optional


def default_applied_ops_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VibeMic"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "vibemic"
    return base / "applied_ops.json"


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AppliedOpLedger:
    """Bounded persistent ledger for stable append-operation ids."""

    def __init__(self, path: Optional[Path] = None, max_entries: int = 4096) -> None:
        self.path = path
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, str | None] = OrderedDict()
        self.unavailable_reason: str | None = None
        self._load()

    @staticmethod
    def _key(client_id: str, op_id: str) -> str:
        return f"{client_id}\u001f{op_id}"

    def status(self, client_id: str, op_id: str, text: str) -> str:
        """Return missing, match, conflict, or unavailable."""
        if self.unavailable_reason is not None:
            return "unavailable"
        key = self._key(client_id, op_id)
        if key not in self._entries:
            return "missing"
        self._entries.move_to_end(key)
        recorded = self._entries[key]
        if recorded is None or recorded == _text_digest(text):
            return "match"
        return "conflict"

    def contains(self, client_id: str, op_id: str) -> bool:
        key = self._key(client_id, op_id)
        if key not in self._entries:
            return False
        self._entries.move_to_end(key)
        return True

    def record(self, client_id: str, op_id: str, text: str = "") -> bool:
        if self.unavailable_reason is not None:
            return False
        key = self._key(client_id, op_id)
        self._entries[key] = _text_digest(text)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        if self._persist():
            return True
        self.unavailable_reason = "persist_failed"
        return False

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, list):
                raise ValueError("ledger_root_must_be_list")
            for item in raw[-self.max_entries :]:
                if isinstance(item, str) and "\u001f" in item:
                    self._entries[item] = None
                elif isinstance(item, dict):
                    key = item.get("key")
                    digest = item.get("digest")
                    if (
                        isinstance(key, str)
                        and "\u001f" in key
                        and isinstance(digest, str)
                        and digest
                    ):
                        self._entries[key] = digest
                    else:
                        raise ValueError("invalid_ledger_entry")
                else:
                    raise ValueError("invalid_ledger_entry")
        except FileNotFoundError:
            return
        except Exception as exc:
            self.unavailable_reason = f"load_failed:{type(exc).__name__}"

    def _persist(self) -> bool:
        if self.path is None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = [
                {"key": key, "digest": digest}
                for key, digest in self._entries.items()
                if digest is not None
            ]
            encoded = json.dumps(payload, ensure_ascii=False) + "\n"
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            if os.name != "nt":
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            return True
        except Exception:
            return False
