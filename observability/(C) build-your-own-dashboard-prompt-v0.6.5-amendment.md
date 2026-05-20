# v0.6.5 Amendment — Telegram `/yes <id>` + `/no <id>` slash commands

Apply on top of current `main` HEAD (post v0.6.4). Phase 2 feature
from the Telegram Remote Trigger PRD — the no-heuristic alternative
to the deferred inline keyboard. Bridge-only — single file change to
`telegram_bridge.py` plus a CHANGELOG entry. No schema changes, no
new endpoints, no breaking changes.

**Numbered `v0.6.5`** — patch over v0.6.4. Same Telegram-bridge
subsystem; smallest scope in the v0.6.x patch sequence.

**Verified against current `main` HEAD**:
- `_CMD_WITH_ID_RE` (post-v0.6.2) matches
  `(answer|reply|approve|cancel|snooze)` — extend alternation with
  `yes|no`.
- `POST /api/decisions/{id}/answer` exists and accepts
  `{answer: string}` body. No API change needed.
- Reply-to-msg routing for `event_type='decision'` already exists
  (v0.3.0) — currently sends body verbatim. v0.6.5 adds a
  strict-match shortcut for `/yes` / `/no` / `yes` / `no` before
  the verbatim fallback.
- No collision with v0.6.0–v0.6.4 surfaces.

---

## Why this release

Inline keyboard `[Yes] [No]` for binary DECISIONS was deferred
(PRD Phase 2) because it needs a "what's binary?" heuristic — the
bridge has to detect which decisions are yes/no before deciding
whether to render buttons. That heuristic doesn't have enough
concrete examples to build yet.

`/yes <id>` and `/no <id>` skip the heuristic entirely. Operator
opts in explicitly per decision: when they see a binary question,
they type two keystrokes instead of typing "yes" or "no" as a free-
text answer. Same payload to the API (`answer: "yes"` or
`"no"`), shorter input on a phone keyboard.

Future v0.7+ inline keyboard can layer on top of this once 10+
real binary DECISION examples have accumulated in `ops_decisions`
— the heuristic gets data, the slash commands stay as the typed
fallback.

## Bridge delta — `telegram_bridge.py`

Single file. No new modules, no new dependencies.

### `_CMD_WITH_ID_RE` — extend pattern

Current (post-v0.6.2):

```python
_CMD_WITH_ID_RE = re.compile(
    r"^/(answer|reply|approve|cancel|snooze)\s+(\d+)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
```

Extend the verb alternation:

```python
_CMD_WITH_ID_RE = re.compile(
    r"^/(answer|reply|approve|cancel|snooze|yes|no)\s+(\d+)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
```

`yes` and `no` keep the body group optional — they take no body.

### `_handle_message` — two new verb arms

In the existing `_CMD_WITH_ID_RE` match block, after the `snooze`
case, add:

```python
if verb in ("yes", "no"):
    return _handle_yes_no(chat_id, task_id, verb.lower())
```

(The `task_id` variable name carries the integer ID — same as
mvp2's `/snooze` case.)

### `_handle_yes_no(chat_id, decision_id, answer)` — new

`answer` is the literal string `"yes"` or `"no"`.

1. **POST `/api/decisions/{decision_id}/answer`** with body
   `{"answer": answer}`. The decisions router already validates
   that the decision exists and is `status='pending'` — relies on
   its 404 / 400 responses.
2. **On 200** → reply `✅ decision #{decision_id} answered: {answer}`.
3. **On 404** → reply `decision #{decision_id} not found`.
4. **On 400** (already answered or wrong state) → reply with the
   API error verbatim (truncated to 500 chars via `_md_safe`).
5. **No audit-log row.** Matches the existing `/answer` slash
   pattern from v0.3.0 — the decisions answer endpoint is the
   audit source. Departs from mvp2's audit-everything-from-Telegram
   policy for `/run`/`/approve`/`/cancel` because those changes
   `ops_tasks` state directly; `/yes`/`/no` go through the existing
   decisions API which already handles its own logging.

### Reply-to-decision shortcut — new

In the reply-to-msg branch of `_handle_message`, when the resolved
event_type is `'decision'`, intercept BEFORE the existing verbatim
routing:

```python
if event_type == 'decision':
    stripped = body.strip().lower()
    if stripped in ('/yes', 'yes'):
        return _handle_yes_no(chat_id, int(event_id), 'yes')
    if stripped in ('/no', 'no'):
        return _handle_yes_no(chat_id, int(event_id), 'no')
    # Existing verbatim fallback continues:
    return _route_reply('decision', int(event_id), body)
```

**Strict whole-message match.** Only the four literals
`/yes` / `/no` / `yes` / `no` (case-insensitive) standalone trigger
the shortcut. A reply like `"Yes, do it"` or `"No — defer"` falls
through to the existing verbatim routing so the operator's nuance
isn't collapsed.

### `/help` text — extend

Append a line to the existing help card:

```
/yes <id> · /no <id> — shortcut to answer a binary decision.
Reply to a ❓ DECISION notification with just /yes or /no (or
bare yes / no) to answer it without typing the id.
```

## Stop conditions

1. **`/yes <id>` happy path.** Seed a pending decision #42. Send
   `/yes 42`. Bridge replies `✅ decision #42 answered: yes`. The
   `ops_decisions` row flips to `status='answered'`, `answer='yes'`.
2. **`/no <id>` happy path.** Same flow with `/no 42`. Result:
   `answer='no'`.
3. **Reply-to-msg `/yes` shortcut.** Seed a pending decision with a
   `notification_log` row (mimic the outbound tick). Reply to that
   message with the body `/yes`. Bridge routes via
   `_lookup_by_tg_message` → resolves decision_id → answers `yes`.
   The API receives the body `yes`, NOT `/yes`.
4. **Reply-to-msg bare `yes` shortcut.** Same as #3 but body is
   `yes` (no slash). Same outcome — answer recorded as `yes`.
5. **Reply-to-msg verbatim fallback preserved.** Reply with
   `Yes, do it` (longer than the bare keyword). Falls through to
   the existing verbatim routing. `answer="Yes, do it"`. No
   collision with the shortcut.
6. **Already-answered decision.** Answer #42 manually
   (`status='answered'`), then send `/yes 42`. Bridge replies with
   the API's 400 error (`decision #42 already answered` or similar
   shape). No state change.
7. **Non-existent decision.** Send `/yes 9999`. Bridge replies
   `decision #9999 not found`. No state change.
8. **Malformed ID.** Send `/yes abc`. Regex doesn't match (verbs
   require `\d+`); message falls through to the `/help` branch as
   per NFR11. Long-poll continues.
9. **Backward compat.** `/answer`, `/reply`, `/run`, `/approve`,
   `/cancel`, `/snooze`, `/status`, `/help`, `/start` all match
   correctly. `_CMD_WITH_ID_RE` still parses
   `/answer 42 some body text` for `answer`/`reply` (the body
   group still works for verbs that need it).

## Order of operations

1. **Extend `_CMD_WITH_ID_RE`** regex. Test the new alternation
   against fixture inputs before wiring — confirm `/yes 42` /
   `/no 42` / `/answer 42 body` all parse correctly.
2. **`_handle_yes_no` function** — thin POST wrapper.
3. **Wire `_handle_message`** — two verb arms (`yes`, `no`).
4. **Reply-to-decision shortcut** — strict whole-message
   recognition before the verbatim fallback.
5. **`/help` text** extension.
6. **Smoke tests** — all 9 stop conditions. Reuse the v0.6.2 /
   v0.6.4 dev smoke shape: `scripts/dev/smoke_v0_6_5.py`,
   stubbed `_send_message`, seeded fixtures (pending + answered
   decisions, with and without matching `notification_log` rows).
7. **CHANGELOG `v0.6.5` entry** flipped from DRAFT to shipped.
   Match v0.6.4 / v0.6.3 style.

## Not in this release (deferred)

- **Inline keyboard `[Yes] [No]`** — still needs the binary
  heuristic. The `/yes` and `/no` commands give the heuristic
  designer real data over time (ops_decisions rows now have
  observed yes/no answers via this slash path). Build inline
  keyboard once 10+ examples accumulate.
- **`/maybe` or other multi-option presets** — operators can use
  `/answer <id> <text>` for nuanced answers. Don't multiply slash
  verbs for every imaginable choice.
- **Decision categorisation in the dashboard** — surfacing which
  decisions historically had binary yes/no answers vs free-text.
  Useful for inline-keyboard heuristic design later but out of
  scope.

## Estimate

~45-60 min. Smallest scope in the arc.

- Regex extension: ~5 min
- `_handle_yes_no` function: ~10 min
- Routing wire + reply-to-decision shortcut: ~10-15 min
- `/help` extension: ~2 min
- Smoke tests (9 stop conditions): ~20-25 min
- CHANGELOG flip: ~5 min

Total under 1h. No schema, no new endpoints, no UI surface, no
operator config — just bridge regex + handler + reply-shortcut.

## Compat note with v0.6.0 – v0.6.4

No collisions:
- v0.6.0 SkillLauncher + `cost_source` untouched.
- v0.5.0-mvp2 `/run` / `/approve` / `/cancel` + audit pattern
  untouched (audit policy preserved: `/yes`/`/no` matches the
  existing `/answer` no-extra-audit pattern, not the v0.6.2
  state-change audit).
- v0.6.1 `/status` untouched.
- v0.6.2 `/snooze` regex sits next to the new `yes`/`no` verbs in
  the same `_CMD_WITH_ID_RE` alternation; all parse-tests verify
  no cross-contamination.
- v0.6.3 multi-account untouched.
- v0.6.4 reply-to-task-complete chain untouched — the
  reply-to-decision shortcut is layered before its branch.

---

End of amendment. Apply against current `main` HEAD (post-v0.6.4).
Quality bar matches v0.6.2 / v0.6.4 — terse, bridge-only, stdlib-
only, predictable shortcut semantics.
