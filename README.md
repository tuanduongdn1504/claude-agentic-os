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

**Tooling** — `cc` shim (`start`, `stop`, `restart`, `status`, `doctor`,
`logs`, `setup otel`, `trigger`, `sync`), idempotent `install.sh` with
`rsync --delete` scoped only to code dirs (your data is never touched),
deterministic `cc doctor` health check, Playwright smoke + screenshot suites.

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

Phase 1, the cosmetic finish, and dispatcher hardening (cost cap / risk
gate / back-pressure) are shipped. See `CHANGELOG.md` for the full
picture. The Telegram bridge is the only major item still deferred.

## License

Apache-2.0. See `LICENSE`.
