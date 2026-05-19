# v0.6.2 Amendment — Telegram `/snooze <decision_id> [duration]`

Apply on top of current `main` HEAD (post v0.6.1). Phase 2 feature
from the Telegram Remote Trigger PRD
(`command-centre/docs/prd-telegram-remote.md`). Bridge-only — single
file change to `telegram_bridge.py` plus a CHANGELOG entry. No schema
changes (column already exists, unused), no new endpoints, no breaking
changes.

**Numbered `v0.6.2`** — patch-level over v0.6.1, same Telegram-bridge
subsystem, even tighter scope. Pattern consistent with v0.6.1.

**Verified against current `main` HEAD**:
- `notification_log.snoozed_until` column already exists in the schema
  (shipped in v0.3.0, never wired up — PRD literally calls it "the
  unused `notification_log.snoozed_until` column").
- `_already_notified` at `telegram_bridge.py:126` currently checks
  only `(event_type, event_key, chat_id)` — does NOT respect
  `snoozed_until`. Fix is part of this release.
- `_CMD_WITH_ID_RE` regex (mvp2) matches `(answer|reply|approve|cancel)`
  — extend to `(answer|reply|approve|cancel|snooze)` since `/snooze`
  takes a decision_id like the others.
- `notification_log` rows for decisions exist with
  `event_type='decision'`, `event_key=str(decision_id)` (per
  `_outbound_tick`'s decision branch).
- No collision with v0.6.1's `_handle_status`, mvp2's other handlers,
  or v0.6.0's surfaces.

---

## Why this release

`/snooze` solves a specific friction: a decision pings, operator can't
answer right now (in a meeting, on a call, mid-sleep), but the outbound
tick re-fires every 30s. Today the operator either answers half-mind or
mutes the entire chat. Neither is the right tool.

`/snooze 42 30m` means "remind me about decision 42 in 30 minutes."
Notification disappears from the re-fire cycle until the window
elapses, then fires again. Operator can re-snooze.

The wire was already designed in v0.3.0 (column + dedupe-check intent
noted in the original CHANGELOG entry) but never built. This release
finishes that work — small scope, clean payoff, lowest-risk Phase 2
candidate after `/status`.

## Bridge delta — `telegram_bridge.py`

Single file. No new modules, no new dependencies.

### `_CMD_WITH_ID_RE` — extend pattern

Current (mvp2):

```python
_CMD_WITH_ID_RE = re.compile(
    r"^/(answer|reply|approve|cancel)\s+(\d+)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
```

Extend the verb alternation:

```python
_CMD_WITH_ID_RE = re.compile(
    r"^/(answer|reply|approve|cancel|snooze)\s+(\d+)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
```

`snooze` keeps the body group optional — `/snooze 42` (no duration) is
valid and defaults to 30m.

### `_handle_message` — fifth routing branch

In the existing `_CMD_WITH_ID_RE` match block, add a sixth verb arm
(after `cancel`):

```python
if verb == "snooze":
    return _handle_snooze(chat_id, task_id, body)  # task_id var carries decision_id
```

(The `task_id` variable name from mvp2's parser is a misnomer here —
it's just the integer ID. Don't rename; the regex group is generic.)

### `_handle_snooze(chat_id, decision_id, duration_str)` — new

1. **Parse duration.** If `duration_str` is None or empty, default to
   `30m`. Otherwise match against `^(\d+)([mhd])$`. On parse failure,
   reply with usage hint: `usage: /snooze <decision_id> [30m|2h|1d]
   — default 30m`.
2. **Cap maximum.** Clamp the parsed duration to `24h`. Anything
   longer → reply `max 24h — capped`. (Operator can re-snooze if they
   genuinely want longer; preventing accidental month-long snooze.)
3. **Look up decision.** `GET /api/decisions/{id}` (or
   `/api/decisions?status=pending` + filter) to verify the decision
   exists and is still pending. 404 → reply `decision #{id} not found`.
   Already answered → reply `decision #{id} already answered`.
4. **Look up notification_log row.** A decision must have been notified
   first to be snoozable. Query
   `SELECT id, snoozed_until FROM notification_log WHERE
   event_type='decision' AND event_key=? AND chat_id=?`. No row →
   reply `decision #{id} not yet notified — cannot snooze`. (Edge
   case: operator runs `/snooze` before the next outbound tick. Tell
   them clearly.)
5. **UPDATE snoozed_until.** Compute `target = now_utc() + duration`
   in ISO8601. `UPDATE notification_log SET snoozed_until=? WHERE
   id=?`. Idempotent: a second `/snooze` resets the timer from now.
6. **Audit log.** INSERT into `activities` with `event_type=
   'decision_snoozed'`, `detail={decision_id, duration_str,
   snoozed_until, source: 'telegram'}`. Matches mvp2's audit pattern
   for state-changing TG commands (FR19).
7. **Reply.**
   `✅ decision #{id} snoozed for {duration} — next re-fire {local_time}`.
   Use the v0.6.1 `_now_local_str()` helper for the timestamp.

### `_already_notified` — respect `snoozed_until`

Current logic blocks unconditionally when a row exists. Update so the
dedupe-block ELAPSES when `snoozed_until <= now()`:

```python
def _already_notified(event_type: str, event_key: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT snoozed_until FROM notification_log "
            "WHERE event_type=? AND event_key=? AND chat_id=? LIMIT 1",
            (event_type, event_key, CHAT_ID),
        ).fetchone()
        if not row:
            return False
        snoozed_until = row[0]
        if snoozed_until is None:
            return True
        # Compare as ISO8601 UTC strings — SQLite default datetime() is
        # lexicographically comparable.
        return snoozed_until > _now_utc_iso()
```

Add a sibling `_now_utc_iso()` helper:

```python
def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
```

(Match the format used by SQLite's `datetime('now')` to keep
comparisons lexicographic.)

### `_record_notify` — clear `snoozed_until` after re-fire

After a snooze elapses and `_already_notified` returns False, the
outbound tick will re-send the notification and call `_record_notify`.
Current `INSERT OR IGNORE` is a no-op when the row already exists —
meaning `snoozed_until` stays at the past timestamp, and the next tick
returns False again, looping the re-fire.

Fix: after a successful send, if the existing row had a non-null
`snoozed_until`, clear it back to NULL. Cleanest is a single UPDATE
after the INSERT-or-IGNORE:

```python
def _record_notify(event_type: str, event_key: str, telegram_message_id: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notification_log "
            "(event_type, event_key, chat_id, telegram_message_id) "
            "VALUES (?, ?, ?, ?)",
            (event_type, event_key, CHAT_ID, str(telegram_message_id)),
        )
        # If the row existed already (e.g., snooze elapsed and we re-fired),
        # clear snoozed_until so dedupe reverts to default "already notified".
        conn.execute(
            "UPDATE notification_log SET snoozed_until=NULL "
            "WHERE event_type=? AND event_key=? AND chat_id=? "
            "AND snoozed_until IS NOT NULL AND snoozed_until <= ?",
            (event_type, event_key, CHAT_ID, _now_utc_iso()),
        )
```

After this, the snooze semantics are: snooze → wait → re-fire once →
permanent dedupe (unless operator snoozes again). Matches "snooze
button" UX on phones.

### `/help` text — extend with the new verb

Add a line documenting `/snooze <id> [duration]` with the duration
format and default.

## Reply-to-message support — optional but recommended

`_lookup_by_tg_message` already routes replies on
decision/inbox/risk_gated. Adding snooze-via-reply is ~10 lines:

In `_handle_message`'s reply-to-msg branch, if the reply body matches
`^/snooze(\s+\d+[mhd])?$` and the looked-up event is a decision, route
to `_handle_snooze(chat_id, int(event_id), <duration_or_None>)`.

Tradeoff: small UX win (operator replies to the decision ping with
just "/snooze 30m" instead of typing the ID), small code addition.
Include in MVP — keeps consistent with mvp2's reply-to-RISK-GATED →
approve pattern.

## Scope — decisions only for MVP

`notification_log` holds rows for four event types: `decision`,
`inbox`, `task_complete`, `risk_gated`. PRD literally specifies
"`/snooze <decision_id>`" — decisions only. Don't generalise yet:

- **Inbox snooze**: agent-to-user messages re-fire every 30s when
  unread. Snooze would help but is rarer (operator usually reads
  inbox once and moves on). Defer until usage signals.
- **Task-complete snooze**: a `done`/`failed` notification fires once
  and never re-fires (no dedupe loop to suppress). Snooze is
  meaningless here.
- **Risk-gated snooze**: re-fires until approve/cancel. Could be
  useful but the right answer is usually `/approve` or `/cancel`,
  not snooze. Defer.

So `/handle_snooze` rejects (with a clear message) when the lookup
finds a non-decision row. Future v0.7+ can generalise.

## Stop conditions

1. **Happy path with default duration.** Send `/snooze 42` (assuming
   decision #42 was notified). Reply: `✅ decision #42 snoozed for
   30m — next re-fire HH:MM GMT+7`. `notification_log.snoozed_until`
   set to now + 30m. Next outbound tick (within 30s) does NOT re-send
   the notification for decision #42.
2. **Explicit duration.** Send `/snooze 42 2h`. Same flow, snoozed_until
   = now + 2h. Reply reflects the new wake time.
3. **Re-snooze.** Send `/snooze 42 30m`, wait 5s, send `/snooze 42 2h`.
   `snoozed_until` advances to now + 2h. No stale 30m timer.
4. **Re-fire after elapse.** Send `/snooze 42 1m`. Wait 90s.
   Outbound tick re-sends the decision notification. After re-fire,
   `snoozed_until` is NULL.
5. **Reply-to-msg snooze.** Reply to a decision notification with
   `/snooze 30m` (no ID). Lookup resolves decision_id from
   `_lookup_by_tg_message`, snoozes correctly.
6. **Malformed duration.** Send `/snooze 42 wat`. Reply with usage
   hint. No DB change. Bridge does not crash.
7. **Cap at 24h.** Send `/snooze 42 7d`. Reply: `max 24h — capped`.
   `snoozed_until = now + 24h`.
8. **Non-existent decision.** Send `/snooze 9999`. Reply: `decision
   #9999 not found`.
9. **Already-answered decision.** Answer decision #42, then `/snooze 42`.
   Reply: `decision #42 already answered`.
10. **Not-yet-notified decision.** A pending decision exists in DB but
    no `notification_log` row (race with first outbound tick). Reply:
    `decision #{id} not yet notified — cannot snooze`.
11. **Audit log.** Each successful `/snooze` writes an `activities`
    row with `event_type='decision_snoozed'`, `source='telegram'`,
    `detail` JSON containing decision_id + duration + snoozed_until.
12. **Backward compat.** `/answer`, `/reply`, `/run`, `/approve`,
    `/cancel`, `/status`, `/help`, `/start` all work unchanged.

## Order of operations

1. **`_now_utc_iso()`** helper — sibling to v0.6.1's `_now_local_str`.
2. **Fix `_already_notified`** to respect `snoozed_until`. Test
   manually with a hand-crafted SQL UPDATE on a real row before
   touching the bridge inbound side.
3. **Fix `_record_notify`** to clear `snoozed_until` after re-fire.
4. **Extend `_CMD_WITH_ID_RE`** regex with `snooze` verb.
5. **`_handle_snooze`** function — duration parser, validation, lookup,
   UPDATE, audit log, reply.
6. **Wire `_handle_message`** — fifth verb arm in the with-ID branch.
7. **Reply-to-msg routing** for `/snooze` in the reply-to-message
   branch (~10 lines).
8. **`/help` text** extension.
9. **Smoke tests** — all 12 stop conditions end-to-end. Follow the
   v0.6.1 pattern: a dev smoke script
   (`scripts/dev/smoke_v0_6_2.py`) drives synthetic Bot API messages
   against a stubbed `_send_message`.
10. **CHANGELOG `v0.6.2` entry** flipped from DRAFT to shipped after
    smoke pass. Match v0.6.1's style — What ships / Verified /
    Operator flow.

## Not in this release (deferred)

- **Generalised snooze** (inbox / risk-gated event types) — defer
  until concrete usage signals demand it.
- **`/snoozes` listing command** showing active snoozes with wake
  times — would be nice but `/status` already gives decision count;
  adding "N snoozed" to `/status` is a v0.7 enhancement, not part of
  this release.
- **Snooze cancellation** (`/unsnooze 42` to clear the timer early) —
  operator workaround is `/answer 42 ...` which clears the dedupe row
  anyway. Not justified yet.
- **Per-event-type snooze policy** (e.g., `risk_gated` cannot be
  snoozed > 1h) — premature. Single 24h cap for now.

## Estimate

~1h:
- `_already_notified` + `_record_notify` fixes + `_now_utc_iso`:
  ~15 min
- Regex extension + `_handle_snooze` + audit log: ~25 min
- Reply-to-msg routing: ~5 min
- Smoke tests (12 stop conditions): ~15 min
- CHANGELOG flip: ~5 min

Smallest Phase 2 win in the corpus. Lower than v0.6.1 (1-2h) because
the heavy lifting (notification_log schema + outbound tick loop +
existing dedupe pattern) already exists from v0.3.0 — this release
just wires up the dormant column and adds one slash command on top.

## Compat note with v0.6.1

No collisions. `/snooze` joins the with-ID family alongside
`/answer|reply|approve|cancel`; `_CMD_STATUS_RE` is untouched. The
`_already_notified` fix improves dedupe semantics globally — affects
decision, inbox, task_complete, and risk_gated event types — but
since `snoozed_until` has been NULL for every existing row since
v0.3.0, the behaviour change is invisible to operators until they
actually use `/snooze`.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.1
merge). Quality bar matches v0.6.1 — terse, bridge-only, mobile-
readable, stdlib-only.
