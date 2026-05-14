# Realtime Cursor Sync

Realtime LAN append-only text sync from phone to the current cursor on Windows and Linux X11.

## Components

- `windows_host/`: Python host app for Windows and Linux X11. Receives text over WebSocket and injects Unicode keystrokes at current cursor.
- `android_client/`: Android app source code for realtime typing and LAN session management.
- `protocol/PROTOCOL.md`: Message protocol and sequencing rules.

## MVP Scope

- Realtime append-at-cursor sync.
- LAN only.
- Plain text characters only (no IME composition, emoji control keys, enter/delete).
- Append-only editing from the phone. Non-append edits stay local to the Android input box.
- Single active phone session.
- Linux paste mode can be explicitly selected in the Android app (`Ctrl+V`, `Ctrl+Shift+V`, or `Shift+Insert`).

## Quick Start (Host)

1. Install Python 3.10+.
2. Install dependency:

   ```bash
   pip install -r windows_host/requirements.txt
   ```

3. Start host:

   ```bash
   python windows_host/run_host.py --bind 0.0.0.0 --port 8765
   ```

4. Open Android app and connect to `ws://<host-ip>:8765/ws`.

## Windows Program Build

Build tray executable:

```powershell
.\scripts\build_windows_host_app.ps1
```

Run produced app:

`windows_host\dist\RealtimeCursorHost\RealtimeCursorHost.exe`

## Validation

One-click acceptance checks (host tests + Android debug build):

PowerShell (Windows):

```powershell
.\scripts\run_acceptance_checks.ps1
```

Bash:

```bash
./scripts/run_acceptance_checks.sh
```

Host-only checks (skip Android build):

```powershell
.\scripts\run_acceptance_checks.ps1 -SkipAndroid
```

```bash
SKIP_ANDROID=1 ./scripts/run_acceptance_checks.sh
```

If your Gradle is not on `PATH`, pass it explicitly:

```powershell
.\scripts\run_acceptance_checks.ps1 -GradleCommand "F:\Code\gui-agent-assistant-android\gradlew.bat"
```

Manual host tests:

```bash
python -m unittest discover -s windows_host/tests -p "test_*.py" -v
```
