---
stepsCompleted: ['step-01-init', 'step-02-discovery', 'step-02b-vision', 'step-02c-executive-summary', 'step-03-success', 'step-04-journeys', 'step-05-domain', 'step-06-innovation-skipped', 'step-07-project-type', 'step-08-scoping', 'step-09-functional', 'step-10-nonfunctional', 'step-11-polish']
inputDocuments:
  - command-centre/scripts/telegram_bridge.py
  - command-centre/scripts/routers/hitl.py
  - command-centre/scripts/routers/system.py
  - README.md
  - CHANGELOG.md
documentCounts:
  briefs: 0
  research: 0
  brainstorming: 0
  projectDocs: 5
workflowType: 'prd'
projectType: 'brownfield'
classification:
  projectType: developer_tool
  projectTypeNote: 'hybrid: web_app + cli_tool under developer_tool umbrella'
  domain: scientific
  domainNote: 'emerging agent-ops sub-niche (no formal CSV match)'
  complexity: medium-high
  projectContext: brownfield
  baseVersion: v0.4.0
---

# Product Requirements Document - claude-agentic-os: Telegram Remote Trigger

**Author:** Tuan
**Date:** 2026-05-07

## Executive Summary

Telegram Remote Trigger extends `claude-agentic-os` so the operator can launch, monitor, and complete headless Claude work entirely from Telegram. The dashboard, dispatcher, and Telegram bridge already shipped (v0.1.0–v0.4.0); the missing piece is an inbound `/run <task>` slash command that POSTs to `/api/tasks` and a complete-event push that closes the feedback loop. Together these unlock an "agent-as-employee" workflow: the operator delegates work from their phone, fields decisions over Telegram replies, and receives a notification when the task is done — without touching the keyboard.

The target user is the solo operator already running `claude-agentic-os` locally — currently using Claude Desktop interactive at the desk, with no work happening when away. The problem is not "missing TG features." It is structural: when the operator leaves the keyboard, no headless agent is running, so the bridge has nothing to push and the phone never rings. The fix is to make Telegram itself the entry point for headless work, not just a notification channel.

### What Makes This Special

This is not a general-purpose Telegram bot or a notification rebrand. The differentiator is composition: every existing hardening guarantee — dispatcher back-pressure, daily cost cap, hard risk gate, PID-validated emergency stop — applies automatically when Telegram becomes a trigger surface. No new safety code needed. The operator gains a remote that respects the same guardrails as the dashboard.

The core insight is operational, not technical: the bridge has a perfect speaker and zero microphone. v0.4.0 showed bridge alive, push pipeline alive, dedupe working — but `notified_24h = 1` because nothing was running headless to be notified about. Adding a microphone (inbound `/run`) costs less than 4 hours of work and unlocks the entire async workflow the bridge was built for.

Differentiation versus alternatives:

- **Versus Claude Desktop on iPad** — Telegram is universally installed, lightweight, and already paired; no separate app, no GUI dependency.
- **Versus pre-scheduled cron** — supports ad-hoc trigger at the moment of intent, not pre-planned slots.
- **Versus generic ChatGPT mobile** — preserves codebase access, dispatcher hardening, local-first privacy.
- **Versus Claude CLI Telegram channel** — that channel does not exist in this install (verified `~/.claude/channels/telegram/` missing) and would still bypass the dispatcher's hardening; this PRD reuses the dispatcher.

### Project Classification

- **Project Type:** developer_tool (hybrid web_app + cli_tool under a single umbrella)
- **Domain:** scientific — emerging agent-ops sub-niche, not a formal CSV match
- **Complexity:** medium-high — concurrent headless processes, IPC via mailbox files + SQLite, async Telegram long-poll, security-sensitive PID-based emergency stop. No regulated-industry compliance.
- **Project Context:** brownfield, base version v0.4.0. Five existing components in scope (dispatcher, bridge, decisions API, system endpoints, dashboard).

## Success Criteria

### User Success

The operator (the only user) can launch, supervise, and close a Claude task entirely from Telegram, with no keyboard contact, on first try.

- **Time-to-trigger:** From "I have an idea" → first character typed in TG → task status `pending` in DB ≤ 60 seconds. Measured by inspecting `created_at` on the first 10 TG-launched `ops_tasks` rows.
- **Aha moment:** First completed task that pings DONE while the operator is away from the desk (coffee shop, walking, in transit). Subjective but binary — either it happened or it did not.
- **Decision-friction:** A Telegram reply to a DECISION ping requires no re-explaining context to the agent. Answers like "yes", "no", "use option B" succeed end-to-end ≥ 90% of the time across the first 20 decisions.
- **Trust:** Operator stops checking the dashboard mid-task to verify "is it actually running?" within 1 week of using the feature.

### Business Success

Single-operator reality — "business" = personal time and money ROI, not revenue.

- **Async hours shifted:** Baseline ~0 min/day of away-from-desk work today → target ≥ 30 min/day of useful delegated work within 2 weeks of launch. Measured by `ops_tasks` rows where `created_at` source = telegram and `started_at` falls outside the operator's at-desk hours (proxy: localtime not in 08:00–18:00 weekday).
- **Cost discipline:** Zero days where TG-launched tasks alone breach `MISSION_CONTROL_DAILY_COST_CAP_USD`. Existing dispatcher cap enforces this; the PRD must not weaken it.
- **No runaway incidents:** Zero cases of a TG-launched task that the operator could not stop via `/api/system/emergency-stop` within 60 seconds.

### Technical Success

- **Backward compatibility:** Existing TG flows (decision push, inbox push, reply-to-message, `/answer`, `/reply`, `/help`) continue to work unchanged. Test by replaying decision #8's notify+answer round-trip post-deploy.
- **Dispatcher integrity:** `/run` produces `ops_tasks` rows that pass through the existing dispatcher hardening unchanged — back-pressure, daily cost cap, hard risk gate, PID validation. No new bypasses.
- **Restart-safe:** Bridge crash mid-`/run` does not double-trigger. Offset file + INSERT-OR-IGNORE on `ops_tasks` is sufficient evidence.
- **Latency:** Task-complete TG push fires within 30s of `ops_tasks.completed_at`.
- **Bounded parsing:** `/run` slash command parser handles malformed input gracefully (empty prompt, > 4000 char prompt, missing args) — never crashes the inbound loop.

### Measurable Outcomes

- 5 successful end-to-end TG-triggered tasks in week 1 post-launch (launch → run → decision answered → done → notify).
- 0 dispatcher safety violations attributable to TG-launched tasks in the first 30 days.
- 0 regressions in existing TG flows (verified by smoke replaying decision/inbox round-trips).
- ≤ 5h dev work to ship MVP (estimate; tracked vs actual in commit messages).

## Project Scoping & Phased Development

### MVP Strategy & Philosophy

**MVP Approach: Problem-Solving MVP.**

This is a problem-solving MVP, not an experience MVP, platform MVP, or revenue MVP. The operator already has Claude Desktop (the experience), runs the entire stack locally (no platform), and earns no revenue (single-operator personal tool). The single problem to solve is: **headless agent work cannot be initiated without keyboard access**.

Validated learning targets:

- Will the operator actually use TG-launched tasks more than 0 times/week after MVP ships? (`Success Criteria → Async hours shifted` measures this.)
- Does the existing dispatcher hardening hold when a non-dashboard surface becomes a trigger source? (Smoke tests + `Backward Compatibility Matrix` measure this.)
- Does mobile-screen Markdown render acceptably for the 4 journey shapes (build wait, device debug, mid-retro glance, late-night mistake)?

**Resource Requirements:** Solo developer (Tuan), ~5h focused dev work. No design, no QA team, no external dependencies. Ship in one sitting if the existing `/api/tasks/{id}/approve` and `/api/tasks/{id}/cancel` endpoints already exist — otherwise +1-2h backend work.

### MVP Feature Set (Phase 1)

**Core User Journeys Supported:**

- Journey 1 (EAS build wait — happy path)
- Journey 2 (Device-in-hand debugging)
- Journey 3 (Mid-retro research delegation)
- Journey 4 (Risk-gate intercept + recovery via `/cancel`)

**Must-Have Capabilities:**

1. Inbound `/run <prompt>` parser
2. Outbound task-complete push (`✅ INBOX task #N done`)
3. Risk-gate intercept for TG-launched tasks (`🛑 RISK-GATED`)
4. Inbound `/approve <id>` and `/cancel <id>` slash commands
5. `created_at_source` field on `ops_tasks` (provenance for analytics)
6. Mobile-readable Markdown formatting (verified by phone-screen visual test of each new message type)
7. Backward-compat: existing decision/inbox flows continue to work

### Post-MVP Features

**Phase 2 (Growth) — ship after MVP holds for ≥ 1 week:**

- `/status` snapshot (live sessions, pending decisions, today cost, free dispatcher slots)
- Inline keyboard `[Yes] [No]` for binary DECISIONS (replaces typing)
- `/snooze <decision_id> <duration>` — wires up the unused `notification_log.snoozed_until` column to suppress repeat pings
- `/schedule "0 9 * * *" <prompt>` — creates an `ops_schedules` row from TG
- Reply-to-task-complete starts a follow-up `/run` (e.g., reply to "✅ task #42 done" with "now write the PR description" → new task #43 with reference to #42's output)

**Phase 3 (Vision) — aspirational, no commitment:**

- Topic-per-task in TG supergroup (one thread per task)
- Risk-gate approval via inline button + signed callback
- Voice memo → STT → `/run` (dictation entry while walking/driving)
- Multi-operator with TG admin roles (`access.json`-style policy)
- Bidirectional schedule control (`/schedules`, `/pause-schedule`, `/run-schedule-now`)
- Mobile-friendly read-only dashboard surface (sister PRD — `/api/tasks/summary` for phone view; deferred from this PRD per Journey 4 footnote)

### Implementation Risk Mitigation Strategy

This section covers project-execution risks (estimate, scope, fatigue). For product-level operational risks (token leak, replay, runaway), see `Domain-Specific Requirements → Risk Mitigations` further down.

**Technical Risks:**

| Risk | Probability | Mitigation |
|---|---|---|
| Dispatcher does not expose `awaiting_approval` state for risk-gated tasks | Medium — needs verification | **Pre-implementation spike (15 min):** check `dispatcher.py` source for the actual state name. If different (e.g., `risk_gated`), use that. If risk-gating is currently a hard reject without an intermediate state, MVP scope expands to add the state. Decision point captured in implementation step 2. |
| `/api/tasks/{id}/approve` and `/api/tasks/{id}/cancel` endpoints don't exist | Medium — verify in `routers/tasks.py` first | **Pre-implementation check (5 min):** grep for endpoint patterns. If absent, add them with existing `routers/tasks.py` patterns. +1-2h to estimate. |
| Mobile Markdown renders poorly (code blocks scroll horizontally, tables truncate) | Low | Manual phone-screen test of each new message format BEFORE shipping. Iterate on `_format_*` helpers if needed. |
| Risk-gate bypass via prompt obfuscation (e.g., "delete branches" → "remove obsolete branches") | Medium | Out of scope for THIS PRD. Existing risk gate signals are operator-tuneable; PRD does not weaken or strengthen them. Tracked as Vision-tier feature (smarter prompt classifier). |

**Market Risks (adapted to single-operator reality):**

| Risk | Probability | Mitigation |
|---|---|---|
| Operator builds it but doesn't use it | Medium | Pin the success metric (≥ 30 min/day async work shifted within 2 weeks) and **revisit after 2 weeks with hard data**. If usage is < 5 min/day, MVP didn't solve the real problem and PRD must be re-discovered. |
| Operator's day shape changes (e.g., job change with no mobile dev) | Low | Feature has zero upkeep cost — bridge keeps running; if unused, no harm. No mitigation required. |

**Resource Risks:**

| Risk | Mitigation |
|---|---|
| Dev work exceeds 5h estimate | **Split MVP into 2 commits.** Commit 1 = outbound task-complete push (1-2h, immediately useful — operator gets notified when scheduled tasks finish). Commit 2 = inbound `/run` + `/approve` + `/cancel` (3-4h, completes the loop). Each commit is independently shippable. |
| `/api/tasks/{id}/approve` not implementable in single sitting | **Descope `/approve` to a help-text response** ("dashboard required to override risk gate"). `/cancel` typically simpler — keep in MVP. Journey 4's recovery degrades to `/cancel` only; user must open dashboard for override. Documented as known MVP limitation. |
| Solo developer fatigue (PRD itself takes too long) | This PRD is itself a sub-1-day artifact. Skip the elicitation/party mode loops if Tuan wants — trust the synthesis quality. (Self-aware: this is the PM agent's risk to manage.) |

## User Journeys

### Persona: Tuan — Mobile developer (React Native / Expo) + Scrum coach

**Situation.** Builds Expo / React Native apps; uses EAS for build + submit; tests on real iOS/Android devices; debugs in Xcode and Android Studio. On the side, facilitates Scrum sessions for teams (retros, sprint planning, coaching notes). Currently runs `claude-agentic-os` locally with Claude Desktop as the primary interactive surface.

**Goal.** Use Claude agents like a junior engineer he texts orders to — during the *idle gaps* of mobile dev (build waits, device-in-hand testing) and during *parallel-attention* moments of coaching (mid-retro research, post-session synthesis).

**Obstacle.** Today, every of these moments is wasted. EAS builds = 15-30 min of doom-scrolling. Device testing with phone in hand = no way to delegate without breaking the test flow. Mid-retro research = either pause the meeting or skip it.

**Solution.** Telegram becomes the entry point — `/run` from anywhere, on any device, in any context, without breaking the primary flow.

---

### Journey 1 — EAS build wait (strongest happy path)

**Opening scene.** Tuan kicks off `eas submit --platform ios` from terminal. Queue shows 18 min ETA. Normally he'd open Twitter or doom-scroll.

**Rising action.** He picks up his phone, opens TG to `@BearCcenterBot`:

```
/run draft release notes for v2.4 from last 2 weeks of git log,
focus on user-facing changes
```

Bot: `✅ task #42 queued · dispatcher pending`. He goes back to watching the iOS upload progress bar in his terminal.

**Climax.** 6 min in, mid-`claude -p` execution, the agent emits:

```
❓ DECISION #15 task #42 — Should I include the dev-only debug
   flag changes? Found 3 commits behind that flag.
```

Tuan, still watching the upload, replies one-handed: "no, dev-only — skip."

**Resolution.** EAS submit completes at 18 min. TG pings 30 seconds before that:

```
✅ INBOX task #42 done · 5m 47s · $0.28
   Saved to docs/release-notes-v2.4.md
```

**The 18-min build wait became 18 min of delegated work + idle.** When Tuan flips to his editor, the release notes draft is already there.

**Capabilities revealed:**

- Inbound `/run` slash parser
- Task-complete outbound push (the missing event in v0.4.0)
- Decision routing while operator is in a different app (Xcode/terminal)
- Dispatcher hardening silently applies (no override needed)

---

### Journey 2 — Device-in-hand debugging, no flow break

**Opening scene.** Reproducing a crash on iPhone 15 connected to Metro. Phone in left hand, tapping through 6 screens to trigger the bug. Bug finally hits — error message is "Failed to fetch", which is unhelpful copy.

**Rising action.** Tuan wants the error message rewritten, but does NOT want to break the device-in-hand repro state to open VS Code. He swipes up to TG and types one-handed:

```
/run rewrite "Failed to fetch" in lib/errors/network.ts to be
actionable — mention checking connection + add retry button copy
```

Bot: `✅ task #51 queued`. He swipes back to the dev build, screenshots the bug, keeps testing.

**Climax.** 4 min later, while he's still in repro flow:

```
❓ DECISION #19 task #51 — Retry button options:
   (a) "Try again"  (b) "Reload"
   Style guide says short verbs. Pick?
```

Reply: "a". Keeps repro-ing. Phone never leaves left hand.

**Resolution.** Done ping arrives while he's drafting the Linear ticket on laptop. The error file is already updated, ready to review.

**Capabilities revealed:**

- One-handed `/run` typing must work — terse syntax, no required args
- Reply-to-message routing on a phone keyboard during deep focus
- Concurrent flows: Metro + dev device + dispatcher task all run together without bridge or dispatcher contention

---

### Journey 3 — Mid-retro research delegation

**Opening scene.** Facilitating a remote sprint retro on Zoom. FigJam board on second monitor. Team mentions a velocity drop over the last 2 sprints. Tuan needs the data NOW — but cannot context-switch out of the meeting without losing facilitation flow.

**Rising action.** He picks up phone under the table:

```
/run check git log + jira issues — when did payment-sdk-v2 ship
and what sprints were affected? quick 5-line summary
```

Bot: `✅ task #58 queued`. He buys time by asking the team a probing follow-up: "Before we attribute the drop to a single cause, what else shifted in those sprints?"

**Climax.** 3 min into the retro, an INBOX message arrives (not a decision — autonomous progress note from the agent):

```
📨 INBOX task #58 — Found: payment-sdk-v2 deployed Sprint 23.
   Sprint 23 + 24 velocity dropped 18%. Full breakdown saving.
```

Tuan glances at his phone. Steers retro: "Team, the data shows velocity dropped right after we deployed payment-sdk-v2. Sprint 23-24, 18% drop. Let's dig into what specifically happened in those sprints."

**Resolution.** The retro becomes 10× more focused. Full breakdown lands in vault by end-of-meeting. Team leaves with a real action item. **He facilitated AND researched simultaneously, no context-switch.**

**Capabilities revealed:**

- INBOX (agent-to-user, not decision) push must arrive promptly during a running task — already implemented in v0.3.0; PRD must regression-test it
- Tasks that produce *reading material* (not action) are valid `/run` shapes — output goes to vault file + a TG summary
- Mobile-readable Markdown matters: under-the-table glances must parse in 3 seconds (limit message length, no excessive code fences)

---

### Journey 4 — Late-night idea + risk gate catches him

**Opening scene.** 11:30pm, on the couch. Idea: clean up stale branches before tomorrow's release.

```
/run delete all stale branches and tag v2.4-rc1 from main
```

Bot: `✅ task #62 queued`. 5 seconds later:

```
🛑 RISK-GATED · task #62 · risk_level=high
   Matched signals: 'delete' + branch ops + tag operation.
   Hard risk gate (MISSION_CONTROL_HARD_RISK_GATE=1) blocked dispatch.
   Reply /approve 62 to override, or /cancel 62 to drop.
```

**Rising action.** Tuan pauses. He was about to run a destructive op while half-asleep. He types `/cancel 62`. Bot: `✅ task #62 cancelled.`

**Climax.** One more message:

```
/run write me a 3-line note "never run destructive git ops from
telegram after 10pm" and save to vault/notes
```

Queued, completes in 2 min, note appears in vault.

**Resolution.** Goes to bed. Morning at desk: runs branch cleanup the proper way, interactive, `git status` visible.

**Capabilities revealed:**

- Risk gate must intercept TG-launched tasks BEFORE dispatcher spawns them — this is the meaning of "Dispatcher integrity" in Success Criteria
- New TG message kind: 🛑 RISK-GATED, visually distinct from ❓ DECISION
- `/approve <id>` and `/cancel <id>` MUST be MVP — without them, Journey 4 has no phone-side recovery and operator must open dashboard, defeating the away-from-keyboard premise

---

### Journey Requirements Summary

| Capability | Journeys | MVP / Growth / Vision |
|---|---|---|
| Inbound `/run <prompt>` parser | 1, 2, 3, 4 | **MVP** |
| Outbound task-complete push | 1, 2 | **MVP** |
| Reply-to-message routing (existing) | 1, 2 | MVP regression-test |
| INBOX (agent-to-user) push mid-task | 3 | Already implemented; regression-test |
| Risk-gate intercept on TG-launched tasks | 4 | **MVP** |
| 🛑 RISK-GATED message kind (distinct format) | 4 | **MVP** |
| `/approve <id>` + `/cancel <id>` | 4 | **MVP (promoted from Growth)** |
| Mobile-readable Markdown formatting | 1, 2, 3 | **MVP** (test on phone) |
| Dashboard sees TG-launched tasks identically | (implicit) | Free, no work |

**Sister capability flagged but OUT OF SCOPE for this PRD:**

Journey not written but mentioned by operator: *"Monitoring dashboard center từ điện thoại thay vì mở trên laptop"* — this is mobile-friendly dashboard viewing, not TG remote trigger. Belongs to a separate PRD (responsive web dashboard or `/status` slash command). Captured here so it does not get lost, but explicitly DEFERRED.

## Domain-Specific Requirements

This product operates in an emerging "agent-ops" domain with no formal regulatory framework. The CSV-mapped `scientific` domain is the closest formal match but does not capture the real concerns. Below are the actual domain-specific requirements for single-operator AI-agent control via public messaging surfaces.

### Compliance & Regulatory

No regulated-industry compliance applies. The system handles no PII, no financial data, no health records, no government data. The operator runs the entire stack on their personal machine; no third-party data processors are involved beyond the Telegram Bot API itself.

The single explicit compliance constraint: **Telegram's API terms of service**. Bot token must remain confidential, message rate must respect Telegram limits (30 msg/sec/chat default), and the bot must not be used for spam or abuse. The current bridge implementation already complies (single chat_id, polling, no broadcast).

### Technical Constraints

**Authentication.** The bridge accepts inbound messages only when `message.chat.id` matches the configured `TELEGRAM_DASH_CHAT_ID`. There is no separate user-level auth — chat membership = full operator authority. This is acceptable for single-operator local-first deployment but **must not** be relaxed without adding an allowlist mechanism (see Vision section: TG admin roles).

**Authorization.** All TG-launched tasks pass through the existing dispatcher hardening:

- `MISSION_CONTROL_MAX_CONCURRENT` (back-pressure) caps concurrent `claude -p` processes regardless of trigger source.
- `MISSION_CONTROL_DAILY_COST_CAP_USD` blocks new task dispatch once the daily cost cap is reached, again regardless of source.
- `MISSION_CONTROL_HARD_RISK_GATE=1` (default) blocks tasks where the prompt matches risk signals (delete/drop/destructive/etc.) until the operator explicitly approves via `/approve <id>`.

The PRD must NOT introduce a code path that bypasses any of the three.

**Token confidentiality.** `TELEGRAM_BOT_TOKEN` is stored in `~/.command-centre/.env` with file mode 600 (verified at install). Bridge loads it once at startup. **Token must never appear in logs, error messages, or any persisted DB row.** Existing bridge code already respects this; PRD changes must preserve it.

**Data scope per message.** A single TG message can carry up to 4096 characters. Decision prompts and inbox messages are truncated to 3000 chars in `_format_decision` / `_format_inbox` to leave room for headers and reply hints. PRD must respect this cap.

**Privacy boundary.** Telegram is a third-party service. Every TG push exposes message content to the Telegram backend, which is outside the local-first privacy boundary of `claude-agentic-os`. The operator implicitly consents to this when configuring the bridge. The PRD must **not** push content the operator has not opted to share — for example, the full body of `claude -p` stdout should NEVER be pushed to TG, only the structured DECISION / INBOX / RISK-GATED summaries the agent emits deliberately.

### Integration Requirements

**Telegram Bot API** — the only external integration. Bridge uses long-poll `getUpdates` (30s timeout) for inbound and `sendMessage` for outbound. Both already battle-tested in v0.3.0.

**`/api/tasks` POST endpoint** — must accept a provenance marker (e.g., `created_at_source: 'telegram'`) so that TG-launched tasks can be filtered later for the "Async hours shifted" success metric. If the endpoint does not currently support a source field, PRD includes adding it as a non-breaking schema change (backfill existing rows to `'dashboard'`).

**`notification_log` table** — extend to recognize new `event_type` values: `task_complete` (Journey 1, 2) and `risk_gated` (Journey 4) on top of existing `decision` and `inbox`. Schema is flexible (event_type is TEXT) — no migration needed.

### Risk Mitigations

This section covers product-level operational risks. For project-execution risks (estimate, scope, fatigue), see `Project Scoping & Phased Development → Implementation Risk Mitigation Strategy` above.

| Risk | Mitigation |
|---|---|
| Bot token leak via logs | Existing bridge masks token; PRD code review must verify no new log lines emit `BOT_TOKEN` value. |
| Compromised chat_id (lost phone) | Operator runs `cc setup telegram` to rotate token; old bot becomes silent. **Out of MVP** — documented as recovery path. |
| TG flood (50+ task-complete events at once) | Outbound tick processes notifications serially with implicit rate limit (one `sendMessage` per call). Telegram's 30 msg/sec limit is well above expected steady-state. |
| Replay of `/run` after bridge crash | `update_id` offset persisted to `.tmp/telegram-offset` after every getUpdates cycle. Bridge restart resumes at last unhandled update — exactly-once for inbound. Outbound dedupe via `notification_log` UNIQUE constraint. |
| Risk-gate bypass attempt | `/approve <id>` is a new slash command; it MUST require the same chat_id check and MUST log the override to `activities` for audit. |
| Runaway TG-launched task | `/api/system/emergency-stop` works regardless of trigger source — kills all `claude -p` PIDs in `PID_DIR` after argv validation. Already shipped. |
| Sensitive content in DECISION prompt | Operator-controlled — agent emits the prompt explicitly. Out of bridge's scope to redact. |

## Project-Type Specific Requirements

### Project-Type Overview

`claude-agentic-os` is a hybrid `developer_tool` (web_app dashboard + cli_tool shim + headless dispatcher daemon). The standard `developer_tool` discovery axes (language support, package managers, IDE integration) don't apply — this is a single-binary local-first command centre, not a library.

The relevant project-type deep-dive for this PRD is **the Telegram slash command surface as a new user-facing API**, plus the bridge state transitions that translate slash commands into dispatcher actions and database writes.

### Technical Architecture Considerations

The PRD adds **one new control surface** (Telegram slash commands) on top of the existing 5-component stack. No new processes, no new daemons, no new persistent state machines. All new logic fits inside the existing `telegram_bridge.py` long-poll loop.

Component impact map:

| Component | Existing role | PRD impact |
|---|---|---|
| `telegram_bridge.py` | TG ↔ API translator | **Modified** — new slash command parsers in `_handle_message`, new outbound event in `_outbound_tick` |
| `routers/tasks.py` (`/api/tasks`) | Task CRUD | **Modified** — accept optional `created_at_source` field |
| `routers/system.py` | System health | Unchanged (already exposes `/api/system/telegram` from v0.4.0) |
| `dispatcher.py` | Spawn `claude -p` | Unchanged — TG-launched tasks pass through unmodified |
| Dashboard UI | Operator surface | Unchanged — TG-launched tasks visible identically |

### Telegram Slash Command Grammar

The bridge defines **5 slash commands** (3 new in MVP, 2 existing). All commands match `^/<verb>(\s+<args>)?$` case-insensitive.

```
EXISTING (v0.3.0, must remain compatible):
  /answer <decision_id> <body>          # answer a pending decision
  /reply  <inbox_id> <body>             # reply to an agent inbox message
  /help, /start, help                   # help text

NEW IN MVP:
  /run <prompt>                         # POST /api/tasks → dispatcher pending
  /approve <task_id>                    # override risk gate for a task
  /cancel  <task_id>                    # cancel a pending or risk-gated task
```

Parser rules:

- All slash commands are **whole-message** matches — partial matches inside a longer text are not commands. Existing `_CMD_RE` regex pattern is extended, not replaced.
- `<prompt>` for `/run` is a free-text remainder, multi-line allowed, capped at 3000 characters before insertion to `ops_tasks.title` / `ops_tasks.prompt`.
- `<task_id>` is a strict positive integer. Non-integer → bot replies with usage hint, no API call.
- Commands without args (`/help`) are unambiguous; commands with required args produce a help reply on missing args, never crash.

### Inbound Message Routing — Decision Tree

The existing routing in `_handle_message` is extended with two new branches:

```
inbound message arrives
├── chat_id != TELEGRAM_DASH_CHAT_ID  →  ignore silently
├── empty text                         →  ignore silently
├── reply_to_message present
│   └── lookup_by_tg_message(reply_to.id)
│       ├── found event_type=decision  →  POST /api/decisions/{id}/answer  ✅
│       ├── found event_type=inbox     →  POST /api/inbox/{id}/reply       ✅
│       ├── found event_type=risk_gated →  treat as /approve <id>          🆕
│       └── not found                  →  reply "couldn't find original"   ✅
├── slash command match
│   ├── /answer <id> <body>            →  POST /api/decisions/{id}/answer  ✅
│   ├── /reply  <id> <body>            →  POST /api/inbox/{id}/reply       ✅
│   ├── /run <prompt>                  →  POST /api/tasks                  🆕
│   ├── /approve <id>                  →  POST /api/tasks/{id}/approve     🆕
│   └── /cancel  <id>                  →  POST /api/tasks/{id}/cancel      🆕
└── /help or /start or "help"          →  send help text                   ✅

✅ = existing in v0.3.0, must regression-test
🆕 = new in MVP
```

### Outbound Message Types — Trigger Conditions

Existing `_outbound_tick` polls 2 sources every 30s. PRD extends this with 2 new sources:

```
EXISTING (v0.3.0):
  /api/decisions?status=pending
    → event_type='decision', format=❓ DECISION
  /api/inbox?unread=1
    → event_type='inbox',    format=📨 INBOX

NEW IN MVP:
  /api/tasks?status_in=done,failed&since=<last_check>
    → event_type='task_complete', format=✅/❌ depending on outcome
  /api/tasks?status=awaiting_approval (or new risk-gated query)
    → event_type='risk_gated', format=🛑 RISK-GATED
```

Each new outbound type:

- Inserts a row into `notification_log` with the new `event_type` value (TEXT column — no migration).
- Truncates message body to 3000 chars per Domain Requirements.
- Records `telegram_message_id` so reply-to-message lookup works for the full lifecycle (operator can reply-to-task-complete to start a follow-up conversation, future Growth feature).

### API Surface Changes

**`POST /api/tasks`** — accept optional `created_at_source` field:

```json
{
  "title": "draft release notes for v2.4",
  "prompt": "...",
  "created_at_source": "telegram"
}
```

Schema change: add `created_at_source TEXT DEFAULT 'dashboard'` column to `ops_tasks`. Backfill existing rows with `'dashboard'`. Non-breaking — all existing dashboard POSTs continue to work without including the field.

**`POST /api/tasks/{id}/approve`** — NEW endpoint. Clears the risk-gate hold on a task. Implementation:

- Verify task exists and is in status `awaiting_approval` (or whatever state the dispatcher uses for risk-gated tasks).
- Transition to `pending` so the dispatcher picks it up on next tick.
- Insert `activities` row: `event_type='task_risk_approved'` with source=`telegram` for audit.

**`POST /api/tasks/{id}/cancel`** — verify it exists; if not, add it. Same pattern as `/approve`. Transitions task to `cancelled`. Logs to `activities`.

### Backward Compatibility Matrix

The PRD must NOT regress these existing flows. All must be smoke-tested post-deploy:

| Existing flow | Test |
|---|---|
| Decision push → user reply-to → API answer | Replay decision #8 round-trip; expect `answer="<test>"`, `status='answered'` |
| Inbox push → user reply → user_to_agent inbox row | Inject inbox row, push, reply via TG, expect new `direction='user_to_agent'` row |
| `/answer <id> <body>` slash command | Send `/answer 8 yes` from TG; expect API call success |
| `/reply <id> <body>` slash command | Send `/reply 7 ok` from TG; expect API call success |
| `/help` and `/start` | Send `/help` from TG; expect help text reply |
| Bridge restart preserves offset | Stop bridge, send TG message, restart bridge; expect message handled exactly once |

### Implementation Considerations

**Sequence of work:**

1. **Schema migration** — add `created_at_source` to `ops_tasks` (small)
2. **Backend endpoints** — `POST /api/tasks/{id}/approve` and `POST /api/tasks/{id}/cancel` if not already present (verify first)
3. **Bridge inbound** — extend `_handle_message` with `/run`, `/approve`, `/cancel` parsers
4. **Bridge outbound** — extend `_outbound_tick` with `task_complete` and `risk_gated` event sources
5. **Format helpers** — `_format_task_complete()` and `_format_risk_gated()` mirroring existing format helpers
6. **Smoke test script** — Bot API round-trip test for each new command
7. **Backward-compat smoke** — replay v0.3.0 flows post-deploy

**Estimated effort:** ~5h for MVP (revised from earlier ~4h due to `/approve` and `/cancel` promotion to MVP).

## Functional Requirements

### Remote Trigger

- **FR1**: Operator can launch a new headless task from Telegram with `/run <prompt>`.
- **FR2**: System confirms acceptance of `/run` with a Telegram reply containing the new task's ID and queue status.
- **FR3**: System rejects malformed `/run` (empty prompt, exceeding length cap) with a usage hint Telegram reply, without crashing the inbound loop.
- **FR4**: Operator can include multi-line prompts in `/run`.

### Remote Decision Handling

- **FR5**: Operator can answer a pending decision via Telegram reply-to-message *(existing in v0.3.0)*.
- **FR6**: Operator can answer a pending decision via `/answer <id> <body>` slash command *(existing in v0.3.0)*.
- **FR7**: Operator can reply to an agent inbox message via Telegram reply-to-message *(existing in v0.3.0)*.
- **FR8**: Operator can reply to an agent inbox message via `/reply <id> <body>` slash command *(existing in v0.3.0)*.

### Operator Notification

- **FR9**: System pushes pending decisions to the operator via Telegram *(existing in v0.3.0)*.
- **FR10**: System pushes unread agent-to-user inbox messages to the operator via Telegram *(existing in v0.3.0)*.
- **FR11**: System pushes task completion (done or failed) events to the operator via Telegram with task ID, duration, cost, and exit status.
- **FR12**: System pushes risk-gated events to the operator via Telegram with a visually distinct format (🛑 RISK-GATED) including the task ID, matched risk signals, and recovery instructions.
- **FR13**: System deduplicates outbound notifications so the operator never receives the same event twice.
- **FR14**: System truncates outbound message content to fit Telegram's 4096-character message limit while preserving headers and reply hints.
- **FR15**: All outbound message formats render readably on a phone-sized screen (verified by manual phone test before MVP ships).

### Risk-Gate Recovery

- **FR16**: Operator can override a risk-gate hold on a task via `/approve <task_id>` slash command.
- **FR17**: Operator can override a risk-gate hold on a task via Telegram reply-to-message on the original 🛑 RISK-GATED notification.
- **FR18**: Operator can cancel a pending or risk-gated task via `/cancel <task_id>` slash command.
- **FR19**: System logs every risk-gate override action to `activities` with source attribution for audit.

### Provenance & Audit

- **FR20**: System records the trigger source (`telegram`, `dashboard`, or other) for every task at creation time.
- **FR21**: System exposes trigger-source data for reporting (queryable by dashboard or analytics).
- **FR22**: System silently ignores inbound Telegram messages from chat IDs other than the configured operator chat.

### Backward Compatibility

- **FR23**: All v0.3.0 Telegram flows (decision push, inbox push, reply-to-message routing, `/answer`, `/reply`, `/help`, `/start`) continue to function unchanged after MVP ships.
- **FR24**: Dashboard task creation continues to work unchanged after MVP ships (no breaking schema change to `/api/tasks` POST body).

### System Integrity (Cross-Surface)

- **FR25**: Dispatcher back-pressure cap (`MISSION_CONTROL_MAX_CONCURRENT`) applies to all tasks regardless of trigger source.
- **FR26**: Dispatcher daily cost cap (`MISSION_CONTROL_DAILY_COST_CAP_USD`) applies to all tasks regardless of trigger source.
- **FR27**: Hard risk gate (`MISSION_CONTROL_HARD_RISK_GATE`) applies to all tasks regardless of trigger source.
- **FR28**: Emergency stop (`/api/system/emergency-stop`) cancels Telegram-launched tasks identically to dashboard-launched tasks.
- **FR29**: Bridge restart recovers from in-flight inbound messages without double-triggering any task or decision (existing `update_id` offset persistence + `notification_log` UNIQUE constraint).
- **FR30**: Bot token never appears in any log output, error message, or persisted database row.

## Non-Functional Requirements

Scalability is skipped because this is a single-operator local-first product with no growth scenario (~10 tasks/day ceiling). Accessibility is skipped because there is no public audience and no compliance regulation. The remaining 5 categories — Performance, Security, Reliability, Integration, Observability — are documented below.

### Performance

- **NFR1** (time-to-trigger): From the moment the operator presses send on a `/run` Telegram message, the corresponding `ops_tasks` row reaches status `pending` in the database within **5 seconds** at the 95th percentile, **15 seconds** at the 99th percentile. Failure mode: bot reply does NOT arrive within 15 s — operator should suspect bridge or network problem.
- **NFR2** (task-complete latency): From the moment `ops_tasks.completed_at` is written, the corresponding ✅/❌ Telegram message arrives within **30 seconds** at the 95th percentile (next outbound tick + Telegram send latency).
- **NFR3** (decision-reply latency): From the moment the operator sends a Telegram reply on a DECISION notification, the corresponding `ops_decisions.status='answered'` write completes within **3 seconds**. This is bounded by the bridge's inbound long-poll cycle which surfaces updates immediately, plus one HTTP POST.
- **NFR4** (mobile-screen render): Each Telegram outbound message format (DECISION, INBOX, TASK COMPLETE, RISK GATED) renders fully without horizontal scrolling on a 390 px × 844 px viewport (iPhone 14/15 reference). Verified manually before MVP ships.

### Security

- **NFR5** (token confidentiality): `TELEGRAM_BOT_TOKEN` lives only in `~/.command-centre/.env` (mode 600) and process memory. Never logged, never persisted to DB, never returned by any API endpoint. Verified by grep audit of bridge code + log files post-deploy.
- **NFR6** (single-chat boundary): Bridge silently ignores all inbound messages where `message.chat.id != TELEGRAM_DASH_CHAT_ID`. Verified by injecting a message from a different chat ID via Bot API and confirming no DB writes, no API calls, no log entries beyond a debug-level "ignored cross-chat".
- **NFR7** (audit completeness): Every operator-initiated state-changing action via Telegram (`/run`, `/approve`, `/cancel`, decision answers, inbox replies) results in at least one `activities` row with explicit `source='telegram'` for forensic reconstruction.
- **NFR8** (dispatcher non-bypass): No code path through the Telegram bridge causes a task to spawn `claude -p` without first passing through `dispatcher.py`'s back-pressure, cost-cap, and risk-gate checks. Verified by code review and by the smoke test in MVP step 6.

### Reliability

- **NFR9** (bridge restart-safe inbound): If the bridge process is killed mid-`getUpdates` cycle and restarted, no inbound Telegram message is processed twice and no message is lost (within Telegram's retention window). Existing offset persistence + INSERT-OR-IGNORE guarantees this; PRD must not regress.
- **NFR10** (bridge restart-safe outbound): If the bridge is killed between `_outbound_tick` queries and `sendMessage` calls, no notification is duplicated. Existing `notification_log` UNIQUE constraint guarantees this; PRD must not regress.
- **NFR11** (graceful degradation under malformed input): Empty `/run`, `/run` with prompt > 3000 chars, non-integer task IDs in `/approve` or `/cancel`, or any malformed payload from the Telegram update stream result in a usage-hint reply (or silent ignore for upstream-malformed data) — never a bridge crash. Verified by deliberately malformed Bot API messages.

### Integration

- **NFR12** (Telegram API rate compliance): Bridge respects Telegram's per-chat rate limit (30 msg/sec default for bots). Steady-state load is far below this; if a burst of task-complete events queues up, the outbound tick processes them serially and never bursts more than 5 messages in any 1-second window.
- **NFR13** (bridge uptime as a daemon): Bridge runs under `launchd` (macOS) with `KeepAlive=true`. If the bridge process exits non-zero, launchd restarts it within ~10 seconds. Operator can verify via `launchctl list | grep telegram-bridge`.

### Observability

- **NFR14** (bridge health endpoint): `GET /api/system/telegram` returns alive/dead status, last outbound timestamp, 24h notification count by type, and last error. Already shipped in v0.4.0; PRD must not regress the response shape.
- **NFR15** (audit trail queryable): All `activities` rows tagged with `source='telegram'` are queryable from the dashboard's existing activity feed without schema migration.
