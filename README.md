# VibeMic

Use your phone as a vibe mic for coding on Windows and Ubuntu over LAN or Tailscale.

VibeMic is built for the remote-work case where desktop audio input is unreliable or unavailable: you are connected to your coding machine through a remote desktop tool such as ToDesk, the remote machine cannot use your local microphone cleanly, but your phone can still reach that machine over the same LAN or a Tailscale network. In that setup, VibeMic turns the phone into a nearby input endpoint and forwards the resulting text to the desktop cursor.

The current MVP is text-first rather than raw-audio transport. The Android side captures text produced on the phone and streams appended text to the desktop host, which injects it at the current cursor position. That makes the project a practical base for "phone as coding mic" workflows while keeping the transport simple and reliable.

## Why this exists

- Remote desktop sessions often break microphone routing or make Bluetooth and USB microphones awkward to use.
- A phone already has a good microphone, battery, and network connectivity.
- Tailscale makes "same LAN" style connectivity practical even when the phone and desktop are not physically colocated.
- For voice-heavy coding workflows, the useful outcome is text appearing at the active cursor with low friction.

## What VibeMic does today

- Runs a Python host on Windows and Linux X11.
- Runs an Android client that sends appended text to the host over WebSocket.
- Injects Unicode text at the current desktop cursor.
- Supports single-device sessions with sequence-checked delivery and heartbeat monitoring.
- Lets the Android client choose Linux paste mode explicitly: `Ctrl+V`, `Ctrl+Shift+V`, or `Shift+Insert`.

## Current scope and limitations

- Current MVP is append-only desktop text insertion.
- Non-append edits stay local to the Android input box.
- Plain text only: no IME composition, emoji control keys, `Enter`, `Delete`, or arrow keys.
- LAN or Tailscale networking only.
- Linux support currently targets X11, not Wayland.
- The project direction is "phone as vibe mic", but the current transport layer sends text, not microphone audio frames.

## Components

- `windows_host/`: Python host app for Windows and Linux X11. Receives text over WebSocket and injects it into the active cursor target.
- `android_client/`: Android app source code for the phone-side input client and session management.
- `protocol/PROTOCOL.md`: Wire protocol and sequencing rules.

## Quick start

1. Install Python 3.10+ on the host machine.
2. Install host dependencies:

   ```bash
   pip install -r windows_host/requirements.txt
   ```

3. Start the host:

   ```bash
   python windows_host/run_host.py --bind 0.0.0.0 --port 8765
   ```

4. Open the Android app and connect to `ws://<host-ip>:8765/ws`.
5. Make sure the phone can reach the host either on the same LAN or through Tailscale.
6. Speak or type on the phone, then let the desktop host inject the resulting text at the active cursor.

## Typical use case

1. Connect to your Windows or Ubuntu coding machine through ToDesk or another remote desktop tool.
2. Join the phone and desktop to the same LAN, or connect both to the same Tailscale tailnet.
3. Start the VibeMic host on the desktop.
4. Connect the Android client from the phone.
5. Use the phone as the nearby input device while coding remotely.

## Ubuntu background service and autostart

On Ubuntu X11, the recommended way to keep the desktop host running in the background is a `systemd --user` service. This lets VibeMic start automatically with your desktop user session, restart after failures, and stay manageable through standard `systemctl` commands.

The repository includes a service template at `scripts/vibemic-host.service`.

Before enabling it, check the session variables that your desktop is currently using:

```bash
echo "$DISPLAY"
echo "$XAUTHORITY"
echo "$XDG_SESSION_TYPE"
```

If needed, edit `scripts/vibemic-host.service` so that `WorkingDirectory`, `ExecStart`, `DISPLAY`, and `XAUTHORITY` match your machine and login session.

Install and enable the service:

```bash
mkdir -p ~/.config/systemd/user
install -m 644 scripts/vibemic-host.service ~/.config/systemd/user/vibemic-host.service
systemctl --user daemon-reload
systemctl --user enable --now vibemic-host.service
```

Verify that it is running:

```bash
systemctl --user status vibemic-host.service --no-pager
curl -fsS http://127.0.0.1:8765/health
```

Useful management commands:

```bash
systemctl --user restart vibemic-host.service
systemctl --user stop vibemic-host.service
journalctl --user -u vibemic-host.service -n 100 --no-pager
```

Important notes:

- This service is intended for a logged-in desktop session, because text injection depends on the active X11 environment.
- The current template targets X11 with `DISPLAY=:1` and `XDG_SESSION_TYPE=x11`.
- If your display number, display manager, or `XAUTHORITY` path changes, update the service file and run `systemctl --user daemon-reload` again.
- This is not a good fit for "boot before login" server-style startup, because VibeMic needs access to your graphical desktop session.

## Windows build

Build the tray executable:

```powershell
.\scripts\build_windows_host_app.ps1
```

Output:

`windows_host\dist\RealtimeCursorHost\RealtimeCursorHost.exe`

## Validation

One-click acceptance checks (host tests and Android debug build):

PowerShell:

```powershell
.\scripts\run_acceptance_checks.ps1
```

Bash:

```bash
./scripts/run_acceptance_checks.sh
```

Host-only checks:

```powershell
.\scripts\run_acceptance_checks.ps1 -SkipAndroid
```

```bash
SKIP_ANDROID=1 ./scripts/run_acceptance_checks.sh
```

If Gradle is not on `PATH`, pass it explicitly:

```powershell
.\scripts\run_acceptance_checks.ps1 -GradleCommand "F:\Code\gui-agent-assistant-android\gradlew.bat"
```

Manual host tests:

```bash
python -m unittest discover -s windows_host/tests -p "test_*.py" -v
```
