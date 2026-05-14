# VibeMic Android Client

Android app for the phone side of VibeMic. It captures text produced on the phone and streams appended text to the desktop host.

## Role in the product

This client is intended to make the phone a practical nearby input device for remote coding sessions. The product goal is "phone as vibe mic"; the current MVP sends text generated on the phone rather than raw microphone audio.

## MVP Behavior

- User configures `host ip` and `port`.
- App connects to `ws://<host>:<port>/ws`.
- Sends `hello`, then `auth`.
- Lets the user explicitly choose Linux paste shortcut mode.
- Each append sends a `text_insert` suffix with strictly increasing `seq`.
- Offline/temporary-send-failure appended suffixes are queued and flushed after re-auth.
- Displays last ACK state and heartbeat RTT.

## Build

Open `android_client` in Android Studio (Hedgehog or newer) and run on a physical Android device.

CLI build:

```bash
# First run only, if wrapper jar does not exist
./scripts/bootstrap_gradle_wrapper.sh

cd android_client
./gradlew :app:assembleDebug
```

## Current Limitations

- Unicode text is accepted; control chars remain blocked in MVP.
- Desktop sync is append-only; delete/replace edits stay local to the Android input box.
- For Linux targets, the app can force `Ctrl+V`, `Ctrl+Shift+V`, or `Shift+Insert` instead of relying on host-side auto detection.
- No control keys (`enter/delete/arrow`) and no IME composition support.
- Networking is limited to LAN or Tailscale-style private connectivity.
