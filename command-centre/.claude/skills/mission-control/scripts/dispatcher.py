"""Dispatcher — spawns `claude -p` children for pending tasks.

Design notes from the prompt that this module enforces:

- **Two execution modes.** `classic` is fire-and-forget; `stream` opens a
  bidirectional stream so the user can reply mid-run and DECISION:/INBOX:
  markers can be parsed live.
- **PID marker files** under `.tmp/mission-control-queue/pids/{pid}` — the
  only reliable way to identify dispatcher-spawned children on macOS 12+,
  because `ps eww` hides env vars. See emergency_stop in the system
  router.
- **Autonomy gate.** Skills declare auto / review / manual. `review` and
  `manual` promote the task to `awaiting_approval` on first pickup; only
  `auto` actually spawns.
- **Fence-aware DECISION: / INBOX: scanning.** Triple-backtick blocks are
  skipped so a literal `DECISION:` appearing inside code doesn't create a
  ghost prompt. Also deduped via the partial UNIQUE index on
  `ops_decisions(session_id, prompt)`.
- **Follow-up queue.** We tail `.tmp/mission-control-queue/{sid}.jsonl`
  and inject each new line into the child's stdin. Offsets are kept in
  memory per-child-run.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# -- Import path wiring so this can run standalone from launchd ---------
_INSTALL_DIR = Path(os.environ.get("CC_INSTALL_DIR") or
                    (Path(__file__).resolve().parents[4]))
_SCRIPTS = _INSTALL_DIR / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import task_tracker  # noqa: E402
import skill_router  # noqa: E402

# -- Constants ----------------------------------------------------------
QUEUE_DIR = Path(os.environ.get("CC_QUEUE_DIR") or
                 (_INSTALL_DIR / ".tmp" / "mission-control-queue"))
PID_DIR = QUEUE_DIR / "pids"
MAX_CONCURRENT = int(os.environ.get("MISSION_CONTROL_MAX_CONCURRENT", "3"))
TASK_TIMEOUT_SECONDS = int(os.environ.get("TASK_TIMEOUT_SECONDS", "1800"))
CLAUDE_CLI = os.environ.get("CLAUDE_CLI_OVERRIDE") or "claude"
DASHBOARD_URL = (
    os.environ.get("CC_DASHBOARD_URL")
    or f"http://{os.environ.get('CC_HOST', '127.0.0.1')}:{os.environ.get('CC_PORT', '8765')}"
)

# Hardening guards (v0.2.0). 0 / unset = disabled.
def _f(name: str, default: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default

DAILY_COST_CAP_USD = _f("MISSION_CONTROL_DAILY_COST_CAP_USD", 0.0)
HARD_RISK_GATE = os.environ.get("MISSION_CONTROL_HARD_RISK_GATE", "1") not in ("0", "false", "no")


# -- Env for spawned children -------------------------------------------

def _build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = DASHBOARD_URL
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/json"
    env["OTEL_METRICS_EXPORTER"] = "otlp"
    env["OTEL_LOGS_EXPORTER"] = "otlp"
    env["OTEL_LOG_TOOL_DETAILS"] = "1"
    env["ATOMICOPS_DISPATCHED"] = "1"  # legacy marker — PID files are the real gate
    if extra:
        env.update(extra)
    return env


# -- PID markers --------------------------------------------------------

def _mark_child_pid(pid: int, task_id: int, mode: str) -> Path:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    marker = PID_DIR / str(pid)
    marker.write_text(json.dumps({
        "pid": pid, "task_id": task_id, "mode": mode,
        "started_at": time.time(),
    }))
    return marker


def _unmark_child_pid(pid: int) -> None:
    try:
        (PID_DIR / str(pid)).unlink(missing_ok=True)
    except Exception:
        pass


def _sweep_stale_pids() -> int:
    """Unlink marker files whose PID is already gone."""
    if not PID_DIR.exists():
        return 0
    swept = 0
    for f in PID_DIR.iterdir():
        if not f.is_file() or not f.name.isdigit():
            continue
        pid = int(f.name)
        try:
            os.kill(pid, 0)
        except OSError:
            f.unlink(missing_ok=True)
            swept += 1
    return swept


# -- Emergency stop check -----------------------------------------------

def _emergency_stop_engaged() -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key = 'emergency_stop'"
        ).fetchone()
    return bool(row and (row["value"] == "1"))


# -- Model resolution ---------------------------------------------------

_SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _skill_frontmatter_model(skill_name: str | None) -> str | None:
    if not skill_name:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT path FROM skills WHERE name = ?", (skill_name,)).fetchone()
    if not row or not row["path"]:
        return None
    try:
        text = Path(row["path"]).read_text(errors="replace")[:4096]
    except Exception:
        return None
    m = _SKILL_FRONTMATTER_RE.match(text)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            if k.strip() == "model":
                return v.strip().strip("'\"")
    return None


def resolve_model(task: dict[str, Any]) -> str | None:
    return (
        task.get("model")
        or _skill_frontmatter_model(task.get("assigned_skill"))
        or os.environ.get("MISSION_CONTROL_DEFAULT_MODEL")
        or None  # let claude CLI pick its default
    )


# -- Autonomy -----------------------------------------------------------

def _skill_autonomy(skill_name: str | None) -> str:
    if not skill_name:
        return "auto"
    with db.connect() as conn:
        row = conn.execute(
            "SELECT autonomy_level FROM skills WHERE name = ?", (skill_name,)
        ).fetchone()
    return (row["autonomy_level"] if row else "auto") or "auto"


# -- DECISION: / INBOX: fence-aware scanner -----------------------------

FENCE_RE = re.compile(r"^\s*```")
DECISION_RE = re.compile(r"^\s*DECISION:\s*(.+?)\s*$")
INBOX_RE = re.compile(r"^\s*INBOX:\s*(.+?)\s*$")


class MarkerScanner:
    """Stateful — drop text blocks in, it emits decisions + inbox messages
    while respecting triple-backtick fenced code blocks."""
    def __init__(self) -> None:
        self._in_fence = False

    def feed(self, text: str) -> tuple[list[str], list[str]]:
        decisions: list[str] = []
        inboxes: list[str] = []
        for line in text.splitlines():
            if FENCE_RE.match(line):
                self._in_fence = not self._in_fence
                continue
            if self._in_fence:
                continue
            md = DECISION_RE.match(line)
            if md:
                decisions.append(md.group(1))
                continue
            mi = INBOX_RE.match(line)
            if mi:
                inboxes.append(mi.group(1))
        return decisions, inboxes


# -- HTTP helpers (tiny — avoids requests dep in the daemon) ------------

def _post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{DASHBOARD_URL}{path}", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# -- Task runners -------------------------------------------------------

def _build_prompt(task: dict) -> str:
    parts = [task.get("title") or ""]
    if task.get("description"):
        parts.append("")
        parts.append(str(task["description"]))
    if task.get("dry_run"):
        parts.append("")
        parts.append("DRY RUN — describe the steps you would take, don't execute.")
    return "\n".join(parts).strip()


def _run_classic(task: dict) -> dict:
    prompt = _build_prompt(task)
    model = resolve_model(task)
    argv = [CLAUDE_CLI, "-p", prompt]
    if model:
        argv += ["--model", model]

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_build_env(),
        start_new_session=True,
    )
    marker = _mark_child_pid(proc.pid, task["id"], "classic")
    out, err = "", ""
    try:
        out, err = proc.communicate(timeout=TASK_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return {"ok": False, "error": "timeout", "stdout": out, "stderr": err,
                "returncode": proc.returncode}
    finally:
        _unmark_child_pid(proc.pid)
        marker.unlink(missing_ok=True)
    return {"ok": proc.returncode == 0, "stdout": out, "stderr": err,
            "returncode": proc.returncode}


def _run_stream(task: dict) -> dict:
    """Stream mode. Parses JSON lines for `session_id` + assistant text,
    scans for DECISION: / INBOX: markers, tails the follow-up mailbox."""
    prompt = _build_prompt(task)
    model = resolve_model(task)
    argv = [CLAUDE_CLI, "-p", prompt, "--output-format", "stream-json"]
    if model:
        argv += ["--model", model]

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        text=True,
        env=_build_env(),
        start_new_session=True,
    )
    marker = _mark_child_pid(proc.pid, task["id"], "stream")

    lines_q: queue.Queue[str | None] = queue.Queue(maxsize=1024)

    def _reader():
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            lines_q.put(line)
        lines_q.put(None)  # EOF

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    scanner = MarkerScanner()
    session_id: str | None = None
    mailbox = QUEUE_DIR / f"{{sid}}.jsonl"  # path-resolved once we know sid
    mailbox_offset = 0
    last_text_buf: list[str] = []
    start_ts = time.monotonic()
    deadline = start_ts + TASK_TIMEOUT_SECONDS

    def _post_inbox(body: str) -> None:
        try:
            _post("/api/inbox", {
                "task_id": task["id"],
                "session_id": session_id,
                "direction": "agent_to_user",
                "body": body,
            })
        except Exception as exc:
            print(f"[dispatcher] inbox post failed: {exc!r}", file=sys.stderr)

    def _post_decision(prompt_text: str) -> int | None:
        try:
            result = _post("/api/decisions", {
                "task_id": task["id"],
                "session_id": session_id,
                "prompt": prompt_text,
            })
            return result.get("id")
        except Exception as exc:
            print(f"[dispatcher] decision post failed: {exc!r}", file=sys.stderr)
            return None

    def _poll_decision(dec_id: int) -> str | None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status, answer FROM ops_decisions WHERE id = ?", (dec_id,)
            ).fetchone()
        if row and row["status"] == "answered":
            return row["answer"]
        return None

    def _drain_mailbox() -> None:
        nonlocal mailbox_offset
        if not session_id:
            return
        mbox = QUEUE_DIR / f"{session_id}.jsonl"
        if not mbox.exists():
            return
        try:
            size = mbox.stat().st_size
            if size <= mailbox_offset:
                return
            with mbox.open("r", errors="replace") as f:
                f.seek(mailbox_offset)
                payload = f.read()
                mailbox_offset = size
            for raw in payload.splitlines():
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                body = obj.get("body") or obj.get("answer") or ""
                if body and proc.stdin:
                    try:
                        proc.stdin.write(body.rstrip() + "\n")
                        proc.stdin.flush()
                    except Exception:
                        pass
        except Exception as exc:
            print(f"[dispatcher] mailbox drain failed: {exc!r}", file=sys.stderr)

    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                break
            if _emergency_stop_engaged():
                proc.kill()
                break
            try:
                line = lines_q.get(timeout=2.0)
            except queue.Empty:
                _drain_mailbox()
                # Drain any pending decision answers (injects on behalf of the user).
                continue
            if line is None:
                break

            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue

            # session id arrives on the init envelope.
            if obj.get("type") == "system" and obj.get("subtype") == "init":
                sid = obj.get("session_id") or obj.get("sessionId")
                if sid and not session_id:
                    session_id = str(sid)
                    task_tracker.update_task(task["id"], session_id=session_id)
                continue

            # Assistant text (prompt-spec "line-based, skip fenced blocks" scan).
            if obj.get("type") == "assistant":
                msg = obj.get("message") or {}
                for block in (msg.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text") or ""
                        last_text_buf.append(text)
                        decisions, inboxes = scanner.feed(text)
                        for d_prompt in decisions:
                            did = _post_decision(d_prompt)
                            if did is None:
                                continue
                            # Block on poll (interval 2s) up to task timeout.
                            answer: str | None = None
                            while time.monotonic() < deadline:
                                answer = _poll_decision(did)
                                if answer is not None:
                                    break
                                time.sleep(2.0)
                            if answer and proc.stdin:
                                try:
                                    proc.stdin.write(answer.rstrip() + "\n")
                                    proc.stdin.flush()
                                except Exception:
                                    pass
                        for ibox in inboxes:
                            _post_inbox(ibox)
                        _drain_mailbox()

            # Final result envelope — capture cost.
            if obj.get("type") == "result":
                task_tracker.update_task(
                    task["id"],
                    cost_usd=obj.get("total_cost_usd"),
                )

        rc = proc.poll()
        out_tail = "".join(last_text_buf)[-4000:]
        return {"ok": (rc == 0 if rc is not None else True), "stdout": out_tail,
                "stderr": "", "returncode": rc, "session_id": session_id}
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        _unmark_child_pid(proc.pid)
        marker.unlink(missing_ok=True)


# -- Orchestration ------------------------------------------------------

def _running_count() -> int:
    """How many ops_tasks are currently in 'running' state."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks WHERE status='running'"
        ).fetchone()
        return int(row["n"]) if row else 0


def _today_cost_usd() -> float:
    """Sum of cost_usd for tasks started today (local time)."""
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS s
            FROM ops_tasks
            WHERE cost_usd IS NOT NULL
              AND started_at IS NOT NULL
              AND DATE(started_at, 'localtime') = DATE('now', 'localtime')
            """
        ).fetchone()
        return float(row["s"]) if row else 0.0


def run_once(verbose: bool = False) -> dict[str, int]:
    """One sweep. Safe to call from heartbeat OR manual `--once`.

    Doesn't actually block on long-running tasks in `classic` mode — but
    it does block on `stream` for the duration of the session (with the
    timeout cap). In practice that's fine because MAX_CONCURRENT keeps
    us from starving the heartbeat cadence.

    Hardening guards (v0.2.0):
    - Back-pressure: cap concurrent runs at MAX_CONCURRENT
    - Daily cost cap: refuse to dispatch when today's spend ≥ DAILY_COST_CAP_USD
    - Hard risk gate: promote risk=high tasks to awaiting_approval if they
      were misconfigured with requires_approval=False
    """
    stats: dict[str, int] = {
        "claimed": 0, "auto": 0, "promoted_for_approval": 0,
        "succeeded": 0, "failed": 0, "swept_pids": 0,
        "risk_gated": 0, "back_pressure": 0, "cost_capped": 0,
    }
    stats["swept_pids"] = _sweep_stale_pids()

    if _emergency_stop_engaged():
        if verbose:
            print("[dispatcher] emergency_stop engaged — not dispatching")
        return stats

    # Guard 1: back-pressure. Free slots = MAX_CONCURRENT − currently running.
    running = _running_count()
    slots = max(0, MAX_CONCURRENT - running)
    if slots == 0:
        stats["back_pressure"] = 1
        task_tracker.log_activity(
            "dispatcher_back_pressure",
            f"running={running} max={MAX_CONCURRENT}",
            metadata={"running": running, "max_concurrent": MAX_CONCURRENT},
        )
        if verbose:
            print(f"[dispatcher] back-pressure: {running}/{MAX_CONCURRENT} running")
        return stats

    # Guard 2: daily cost cap. Off when DAILY_COST_CAP_USD == 0.
    if DAILY_COST_CAP_USD > 0:
        today = _today_cost_usd()
        if today >= DAILY_COST_CAP_USD:
            stats["cost_capped"] = 1
            task_tracker.log_activity(
                "dispatcher_cost_capped",
                f"today=${today:.2f} cap=${DAILY_COST_CAP_USD:.2f}",
                metadata={"today_cost_usd": today, "cap_usd": DAILY_COST_CAP_USD},
            )
            if verbose:
                print(f"[dispatcher] cost-capped: ${today:.2f} ≥ ${DAILY_COST_CAP_USD:.2f}")
            return stats

    claimed = task_tracker.claim_pending(max_rows=slots)
    stats["claimed"] = len(claimed)

    for task in claimed:
        # Guard 3: hard risk gate. risk=high MUST require approval — even
        # if the task was created with requires_approval=False (treated as
        # operator misconfiguration). Promote BEFORE skill auto-assign so
        # operator sees the raw task as queued it.
        risk = (task.get("risk_level") or "").lower()
        if HARD_RISK_GATE and risk == "high" and not task.get("requires_approval"):
            task_tracker.update_task(
                task["id"], status="awaiting_approval",
                started_at=None, requires_approval=1,
            )
            stats["risk_gated"] += 1
            stats["promoted_for_approval"] += 1
            task_tracker.log_activity(
                "task_risk_gated",
                f"task_id={task['id']} risk=high requires_approval=False → promoted",
                metadata={"task_id": task["id"], "risk_level": "high"},
            )
            continue

        # Auto-assign a skill if missing.
        if not task.get("assigned_skill"):
            picked = skill_router.pick(task.get("title") or "", task.get("description"))
            if picked:
                task["assigned_skill"] = picked
                task_tracker.update_task(task["id"], assigned_skill=picked)

        # Autonomy gate.
        autonomy = _skill_autonomy(task.get("assigned_skill"))
        if autonomy in ("review", "manual"):
            task_tracker.update_task(task["id"], status="awaiting_approval",
                                     started_at=None)
            stats["promoted_for_approval"] += 1
            task_tracker.log_activity(
                "task_promoted_for_approval",
                f"task_id={task['id']} skill={task.get('assigned_skill')} autonomy={autonomy}",
            )
            continue

        stats["auto"] += 1
        start = time.monotonic()
        mode = task.get("execution_mode") or "stream"
        try:
            result = _run_stream(task) if mode == "stream" else _run_classic(task)
        except Exception as exc:
            task_tracker.fail_task(task["id"], f"dispatcher crash: {exc!r}")
            stats["failed"] += 1
            continue

        elapsed_ms = int((time.monotonic() - start) * 1000)
        summary = (result.get("stdout") or "").strip().splitlines()
        summary_head = "\n".join(summary[-20:]) if summary else None

        if result.get("ok"):
            task_tracker.complete_task(
                task["id"],
                output_summary=summary_head,
                session_id=result.get("session_id"),
                duration_ms=elapsed_ms,
            )
            stats["succeeded"] += 1
        else:
            err = result.get("error") or f"rc={result.get('returncode')}"
            stderr_tail = (result.get("stderr") or "").strip().splitlines()[-5:]
            msg = f"{err}\n{chr(10).join(stderr_tail)}" if stderr_tail else str(err)
            task_tracker.fail_task(task["id"], msg,
                                   session_id=result.get("session_id"),
                                   duration_ms=elapsed_ms)
            stats["failed"] += 1

    return stats


# -- CLI ----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="(default) run one sweep and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    out = run_once(verbose=args.verbose)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
