# VibeMic Lossless Protocol (v1.1)

This is the canonical protocol for VibeMic Android 1.1.0 and newer. The older `PROTOCOL.md` remains as the legacy v1 reference.

Transport is WebSocket on `/ws`. Authentication fields are required on authenticated messages; their values are intentionally omitted from examples in this document.

## Connection

The client first sends `hello` with a stable `client_id` and app version, then authenticates and receives a new session identifier plus heartbeat interval.

The Android client persists `client_id` across app restarts. A new authentication replaces the previous active session.

Default heartbeat interval is 5 seconds. Default session timeout is 120 seconds. A client may re-authenticate on the same WebSocket after session expiry.

### Multi-endpoint server identity (Android 1.2+)

`hello_ok` includes a persistent `server_id` plus a human-readable `server_name`. Android pins the server identity after a successful connection and treats multiple IP addresses as routes to that same logical host only when the returned identity matches. In Auto mode, an mDNS-discovered `_vibemic._tcp` LAN endpoint is preferred, followed by the last successful endpoint, the configured bootstrap address, known verified endpoints, and built-in fallbacks. Failure of one route moves to the next candidate without discarding the durable delivery queue. On Android, direct NJU/Cosec connections are bound to the physical Wi-Fi network when available so a default VPN route does not unnecessarily capture local traffic.

## Append operation

Client to host:

```json
{
  "type": "text_insert",
  "session_id": "session-uuid",
  "seq": 12,
  "op_id": "stable-operation-uuid",
  "text": "newly appended text",
  "paste_mode": "ctrl_shift_v",
  "ts": 1735689600123
}
```

The session authentication field is omitted from the example above.

Rules:

- `seq` increases by exactly one within one authenticated session.
- `op_id` stays unchanged when one appended chunk is retried after reconnect or a lost ACK.
- `text` is a non-empty UTF-8 suffix.
- Transport-level newline, carriage-return, tab, and backspace control characters remain rejected. Android normalizes those IME characters locally before transport rather than rolling the draft back.
- `paste_mode` is optional and overrides the session default for that chunk.
- The host keeps a bounded persistent `(client_id, op_id)` ledger.
- If an already-applied `op_id` is safely retried with the same text, the host returns success with `duplicate: true` and does not inject the chunk a second time.
- Reusing an existing `op_id` with different text is rejected as `op_id_conflict` before the session sequence is consumed.
- If the persistent dedupe ledger cannot be trusted or written, op-id delivery fails closed as `dedupe_ledger_unavailable` instead of risking duplicate injection.

## ACK

Normal successful append:

```json
{
  "type": "ack",
  "seq": 12,
  "op_id": "stable-operation-uuid",
  "applied_ts": 1735689600139,
  "ok": true
}
```

Successful duplicate retry:

```json
{
  "type": "ack",
  "seq": 1,
  "op_id": "stable-operation-uuid",
  "applied_ts": 1735689601139,
  "ok": true,
  "duplicate": true
}
```

Rejected operations use `ok: false` and a short `reason`.

The lossless Android client removes a queued append only when the successful ACK matches both the current `seq` and `op_id`.

## Android durability rules

The Android client maintains three separate pieces of local state:

1. **Draft** — exactly what the user currently sees in the phone text box.
2. **Processed baseline** — the prefix already converted into durable delivery operations.
3. **Delivery FIFO** — stable `op_id + text` records that have not yet received matching success ACKs.

Required behavior:

- Draft changes are persisted locally.
- A new append is written to the delivery FIFO before network send.
- Only one operation is in flight at a time.
- The queue head is removed only after a matching success ACK.
- ACK timeout, WebSocket failure, stale session, desktop-host restart, Activity restart, or Android-process restart keeps the operation queued.
- A recovered draft tail that was not yet converted into a queue record is converted into one before further delivery.
- Stale WebSocket callbacks from superseded connections are ignored.
- IME control characters are normalized locally; they never trigger restoration of an older snapshot.
- A non-append local edit is preserved on the phone and becomes the new baseline. Only subsequent appended text is sent.
- Clearing the local draft requires explicit confirmation. It does not erase already injected desktop text and does not silently discard queued operations.

## Idempotency boundary

Stable `op_id` plus the host ledger makes normal ACK-loss/reconnect retries idempotent, including retries after a host restart once the successful operation has been recorded in the ledger.

There is an unavoidable tiny crash window between an external desktop injection completing and its dedupe record being persisted. VibeMic therefore does not claim transactional exactly-once behavior across an arbitrary process kill at that exact instant; it is designed to avoid silent loss and ordinary duplicate replay during network/session failures.

## Legacy compatibility

Legacy clients may omit `op_id`; the host still accepts those messages using sequence validation, but they do not receive cross-session deduplication guarantees.

The legacy `text_replace` path remains available for compatibility and is not the primary Android 1.1.0 synchronization path.
