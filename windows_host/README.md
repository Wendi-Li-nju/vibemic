# Host Service

Python host service that receives mobile text over WebSocket and injects appended text into the active cursor target on Windows and Linux X11.

## Features

- `hello -> auth -> text_insert -> ack` append-only main protocol.
- Token-based authenticated session establishment.
- Strict sequence ordering (`seq` must increment by 1).
- Unicode text validation (control chars blocked in MVP scope).
- Heartbeat and session timeout.
- Single active phone session.

## Run

```bash
pip install -r requirements.txt
python run_host.py --bind 0.0.0.0 --port 8765
```

## Platform Notes

- Windows uses `SendInput(KEYEVENTF_UNICODE)` for direct Unicode injection.
- Linux currently supports X11 sessions via a clipboard-paste-first stack with fallbacks.
- Linux append sync prefers clipboard paste, can try AT-SPI append, and finally falls back to direct X11 key injection.
- The Android app can explicitly select Linux paste mode (`Ctrl+V`, `Ctrl+Shift+V`, `Shift+Insert`) instead of relying only on host-side auto detection.
- Wayland sessions are not supported by the built-in injector.

## Tray App (Windows)

Run as a tray program:

```powershell
pip install -r requirements.txt
pip install -r requirements-tray.txt
python run_tray.py
```

Tray menu supports start/stop service and status view.

## Build EXE

```powershell
..\scripts\build_windows_host_app.ps1
```

Output executable:

`windows_host\dist\RealtimeCursorHost\RealtimeCursorHost.exe`

On startup, the host logs the endpoint.

## Endpoints

- `ws://<host>:8765/ws` WebSocket endpoint.
- `http://<host>:8765/health` health and runtime stats.

## Injection Behavior

- Uses `SendInput(KEYEVENTF_UNICODE)` for character injection.
- Requires a visible, non-minimized foreground window with a valid text cursor.
- If no valid foreground window exists, host sends failed `ack`.
