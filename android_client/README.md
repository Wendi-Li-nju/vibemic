# VibeMic Android Client

Android app for the phone side of VibeMic. It captures text produced on the phone and streams appended text to the desktop host.

## Role in the product

This client is intended to make the phone a practical nearby input device for remote coding sessions. The product goal is "phone as vibe mic"; the current MVP sends text generated on the phone rather than raw microphone audio.

## MVP Behavior

- User configures `host ip` and `port`.
- App connects to `ws://<host>:<port>/ws`.
- Sends `hello`, then `auth`.
- Lets the user explicitly choose Linux paste shortcut mode.
- Each append is first written to a persistent FIFO, then sent as a `text_insert` suffix with strictly increasing `seq` and a stable `op_id`.
- Only one append is in flight; an item leaves the queue only after a matching successful ACK.
- Offline, session-expired, ACK-timeout, stale-socket, and app-restart paths preserve the draft and pending queue and recover automatically.
- Local drafts and sync baselines are persisted so long IME/voice input is not lost if the Activity or process restarts.

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

- Unicode text is accepted. IME control characters such as line breaks and tabs are normalized locally and never cause the draft to roll back.
- Desktop sync is append-only; delete/replace edits stay local, become the new baseline, and future appended text can continue syncing.
- For Linux targets, the app can force `Ctrl+V`, `Ctrl+Shift+V`, or `Shift+Insert` instead of relying on host-side auto detection.
- No desktop control-key transport (`Enter`, `Delete`, arrow keys). IME composition is retained locally and synced only after composition commits.
- Networking is limited to LAN or Tailscale-style private connectivity.
