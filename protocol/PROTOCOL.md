# Protocol Specification (v1)

Transport: WebSocket (`/ws`) over LAN.

All messages are UTF-8 JSON objects with field `type`.

## Message Types

### 1) `hello` (client -> host)

```json
{"type":"hello","client_id":"android-uuid","app_ver":"1.0.0"}
```

### 2) `auth` (client -> host)

```json
{"type":"auth","paste_mode":"ctrl_v"}
```

Optional `paste_mode` values:

- `auto`
- `ctrl_v`
- `ctrl_shift_v`
- `shift_insert`

### 3) `auth_ok` (host -> client)

```json
{
  "type":"auth_ok",
  "session_id":"uuid",
  "token":"opaque-token",
  "paste_mode":"ctrl_v",
  "heartbeat_interval_ms":5000
}
```

### 4) `text_insert` (client -> host, append mode)

```json
{
  "type":"text_insert",
  "session_id":"uuid",
  "token":"opaque-token",
  "seq":12,
  "text":"a",
  "paste_mode":"ctrl_shift_v",
  "ts":1735689600123
}
```

Rules:

- `seq` must strictly increase by exactly 1.
- `text` must be non-empty UTF-8 string, and cannot contain `\n`, `\r`, `\t`, backspace.
- Host applies message only when authenticated session/token matches.
- Android sends `text_insert` as the primary sync path.
- Each `text_insert` payload is the newly appended suffix only. The host injects it immediately at the current cursor.
- `paste_mode` is optional on each `text_insert`. If present, it overrides the session default for that appended chunk.

### 5) `text_replace` (client -> host, legacy append-only snapshot sync)

```json
{
  "type":"text_replace",
  "session_id":"uuid",
  "token":"opaque-token",
  "seq":13,
  "text":"hello中",
  "ts":1735689601123
}
```

Rules:

- `seq` must strictly increase by exactly 1.
- `text` may be empty string (used for clear), cannot contain `\n`, `\r`, `\t`, backspace.
- `text_replace` is retained only for legacy compatibility.
- Host treats `text_replace` as the latest full client snapshot for an append-only session.
- Host may debounce a burst of snapshots and apply only the latest one after a short quiet window.
- If the latest accepted snapshot starts with the previously acknowledged snapshot, host injects only the appended suffix at the current cursor.
- Non-append edits are rejected with `reason: "non_append_edit_unsupported"` and should be handled by resetting the client session.

### 6) `ack` (host -> client)

```json
{
  "type":"ack",
  "seq":12,
  "applied_ts":1735689600139,
  "ok":true
}
```

For rejected insert:

```json
{
  "type":"ack",
  "seq":12,
  "applied_ts":1735689600139,
  "ok":false,
  "reason":"out_of_order"
}
```

### 7) `ping` / `pong`

Client:

```json
{"type":"ping","session_id":"uuid","token":"opaque-token","ts":1735689600000}
```

Host:

```json
{"type":"pong","ts":1735689600000,"server_ts":1735689600010}
```

## Session Rules

- Host keeps one active authenticated session.
- New successful `auth` evicts old session.
- Session times out after 15 seconds without valid `ping`.

## Android Client Behavior

- The Android input box is append-only with respect to desktop sync.
- The Android app can explicitly choose Linux paste shortcut mode for the session or each appended chunk.
- If the user performs a non-append edit locally, the client must not send a delete or replace operation to the host.
- Clearing the local Android input box does not clear previously injected desktop text.
