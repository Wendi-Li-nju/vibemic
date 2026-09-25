from __future__ import annotations

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


class AppliedOpLedger:
    def __init__(self, path: Optional[Path] = None, max_entries: int = 4096) -> None:
        self.path = path
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, None] = OrderedDict()
        self._load()

    @staticmethod
    def _key(client_id: str, op_id: str) -> str:
        return f"{client_id}\u001f{op_id}"

    def contains(self, client_id: str, op_id: str) -> bool:
        key = self._key(client_id, op_id)
        if key not in self._entries:
            return False
        self._entries.move_to_end(key)
        return True

    def record(self, client_id: str, op_id: str) -> bool:
        key = self._key(client_id, op_id)
        self._entries[key] = None
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return self._persist()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, list):
                return
            for item in raw[-self.max_entries :]:
                if isinstance(item, str) and "\u001f" in item:
                    self._entries[item] = None
        except FileNotFoundError:
            return
        except Exception:
            return

    def _persist(self) -> bool:
        if self.path is None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(list(self._entries.keys()), ensure_ascii=False) + "\n")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            return True
        except Exception:
            return False
