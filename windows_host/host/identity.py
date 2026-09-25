from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional


def default_server_id_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VibeMic"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "vibemic"
    return base / "server_id"


def load_or_create_server_id(path: Optional[Path] = None) -> str:
    if path is None:
        return str(uuid.uuid4())
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return str(uuid.UUID(raw))
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        pass

    server_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(server_id + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return server_id
