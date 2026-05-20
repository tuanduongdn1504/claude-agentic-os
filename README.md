# claude-agentic-os

A local, single-binary command centre for Claude Code: observability dashboard,
human-in-the-loop task queue, and a Mission Control dispatcher that runs
`claude -p` jobs on your behalf — all driven by the same SQLite that already
collects your OTEL telemetry and JSONL session logs.

Runs at `http://127.0.0.1:8765`. No cloud, no account, no outbound traffic.

## What's in the box

**Dashboard** — 24 wired panels across three pages, dark theme, framer-motion,
TanStack Router, React Query polling. KPI strip, attention bar, token/cost
breakdown, cache hit rate, session outcomes, tool latency p50/p95, hook
activity, project + agent fanout, edit-acceptance, productivity, system
pressure, MCP server drill-down, skills registry with autonomy controls,
context health, OTEL firehose with pause/resume, full sessions table.

**Mission Control** — atomic task claim (BEGIN IMMEDIATE), per-PID marker
files, fence-aware `DECISION:` / `INBOX:` scanner with stdin-mailbox reply
loop, schedules with NL→cron parser, full emergency-stop with argv-verified
PID kill (defends against PID recycling), launchd integration.

**Backend** — FastAPI + raw SQLite (WAL), 18-table schema mirroring real
Claude Code data shapes (subagents tracked, system events captured for
PressurePanel), OTLP/HTTP JSON ingest, JSONL scraper, hardcoded model price
table for cost derivation when sessions don't emit `result` envelopes.

**Telegram bridge** — optional. Forwards pending decisions + unread
inbox messages from Claude Code sessions to your Telegram chat;
routes your replies (reply-to-msg or `/answer` / `/reply` slash
commands) back to the dashboard. Stdlib-only daemon, no
`python-telegram-bot` dependency. `cc setup telegram` runs the
wizard.

**Tooling** — `cc` shim (`start`, `stop`, `restart`, `status`, `doctor`,
`logs`, `setup otel`, `setup telegram`, `trigger`, `sync`), idempotent
`install.sh` with `rsync --delete` scoped only to code dirs (your data
is never touched), deterministic `cc doctor` health check, Playwright
smoke + screenshot suites.

## Install

```bash
bash command-centre/install.sh \
  --project-root="$HOME/path/to/your/.claude/project" \
  --port=8765
```

Flags: `--no-otel` skips the wizard · `--no-launchd` skips LaunchAgent
registration · `--no-build-ui` reuses an existing `ui/dist` · `--yes` is
non-interactive · `--force` wipes only the code dirs · re-runnable; the
venv, DB, logs, and `.env` are preserved.

After install:

```bash
cc doctor      # verify the install
cc start       # launch the server
cc trigger     # poke the dispatcher manually
cc logs        # tail server + mission-control logs
```

### Multi-account setup (optional)

If you log into Claude Code under more than one OAuth account on the same
machine (e.g. work on Desktop, personal on VS Code), `/api/summary` splits
today's spend per account on the dashboard's **Account split** card.

1. Copy the template and edit the entrypoints / UUIDs for your accounts:

   ```bash
   cp ~/.command-centre/data/accounts.json.example ~/.command-centre/data/accounts.json
   $EDITOR ~/.command-centre/data/accounts.json
   ```

   The entrypoint proxy (`claude-code`, `claude-desktop`, `vscode`) works
   retroactively against existing sessions. UUIDs are filled in by the
   optional `SessionStart` hook below and are authoritative when present.

2. *(Optional, recommended for accuracy)* register the SessionStart hook
   so new sessions snapshot their OAuth account UUID into
   `~/.command-centre/data/account-hints/<sid>.json`:

   ```json
   {
     "hooks": {
       "SessionStart": [
         {
           "matcher": "*",
           "command": "/Users/<you>/.command-centre/scripts/hooks/session_start_account_snapshot.py"
         }
       ]
     }
   }
   ```

   Add this to `~/.claude/settings.json`, then restart Claude Code so the
   hook fires for new sessions.

3. Backfill `account_id` on existing rows and reload the dashboard:

   ```bash
   cc sync
   ```

   The card populates within one sync cycle.

Skip this entirely and the dashboard still works — sessions surface under
a single `unknown` row.

### Sync lag (experimental)

By default the server polls `~/.claude/projects/*.jsonl` every 120
seconds. Setting `CC_USE_FSEVENTS=1` in `~/.command-centre/.env` switches
to a `watchdog`-backed FSEvents observer that fires within ~2 seconds of
a JSONL write. The polling loop is the proven, zero-dep path; FSEvents is
opt-in until it has accumulated more soak time.

## Repo layout

```
command-centre/        the product
├── scripts/           FastAPI app, sync workers, doctor, OTEL wizard
├── .claude/skills/    mission-control dispatcher + heartbeat + skill_router
├── ui/                React + Vite dashboard (TS, Tailwind, TanStack Router)
├── templates/         launchd plists (sed-templated by install.sh)
├── docs/              schema-recon notes
├── install.sh         one-command installer
└── cc                 shim — installed at $INSTALL_DIR/bin/cc

observability/         the original prompt + reference guide that drove
                       the build (kept here for context — see also CHANGELOG)
```

## Status

Phase 1, the cosmetic finish, dispatcher hardening, and the Telegram
bridge are all shipped — the original 33-panel spec + the deferred
list are closed. See `CHANGELOG.md` for the full release log.

## License

Apache-2.0. See `LICENSE`.
