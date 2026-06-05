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
# v0.7.4 — when a child `claude -p` hits a usage/rate limit (HTTP 429) the task
# is NOT broken; it just needs to wait for the quota to reset. Re-queue it with
# this backoff (seconds) rather than terminal-failing it. Override via env.
RATE_LIMIT_RETRY_BACKOFF_S = int(os.environ.get("RATE_LIMIT_RETRY_BACKOFF_S", "900"))
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


def _task_cwd() -> str | None:
    """Working dir for a dispatched child. Tasks must run in the operator's
    project (CC_PROJECT_ROOT), NOT the install dir — the launchd agent's
    WorkingDirectory is the install dir, so without an explicit cwd the child
    inherits it (v0.7.3, Bug A). Falls back to None (inherit) when unset or
    missing, logging loudly on a missing dir: a stale CC_PROJECT_ROOT (e.g. a
    deleted worktree) would otherwise silently run every task in the wrong
    place."""
    root = (os.environ.get("CC_PROJECT_ROOT") or "").strip()
    if not root:
        return None
    if not os.path.isdir(root):
        print(f"[dispatcher] CC_PROJECT_ROOT is not a directory: {root!r} — "
              f"falling back to the install dir for this task", file=sys.stderr)
        return None
    return root


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


# v0.7.0 — per-skill adversarial review opt-in. Mirrors _skill_autonomy: a
# single-column lookup. `None`/missing skill → False (off). review_mode is the
# ONLY trigger for the review gate — success_criteria alone never triggers it.
def _skill_review_mode(skill_name: str | None) -> bool:
    if not skill_name:
        return False
    with db.connect() as conn:
        row = conn.execute(
            "SELECT review_mode FROM skills WHERE name = ?", (skill_name,)
        ).fetchone()
    return bool(row and row["review_mode"])


# v0.6.7 — per-skill daily cost budget. Returns (budget, today_spend) where
# `budget is None` means no cap is set. Post-hoc shape matches the global cap:
# we refuse when `today_spend >= budget`, never preemptive.
def _skill_budget_state(skill_name: str | None) -> tuple[float | None, float]:
    if not skill_name:
        return None, 0.0
    with db.connect() as conn:
        row = conn.execute(
            "SELECT daily_budget_usd FROM skills WHERE name = ?", (skill_name,)
        ).fetchone()
        if not row or row["daily_budget_usd"] is None:
            return None, 0.0
        spend_row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS s
            FROM ops_tasks
            WHERE assigned_skill = ?
              AND cost_source = 'api_pool'
              AND cost_usd IS NOT NULL
              AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
            """,
            (skill_name,),
        ).fetchone()
    return float(row["daily_budget_usd"]), float(spend_row["s"] if spend_row else 0.0)


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
    # v0.7.0 — retry composition. When a prior reviewer rejected this work
    # (review_count > 0 with stored feedback), prepend the reason so the re-run
    # addresses it. Mirrors the v0.6.4 follow-up composition: SAME task id,
    # feedback appended — it does not create a new task.
    if (task.get("review_count") or 0) > 0 and task.get("review_feedback"):
        parts.append("")
        parts.append("PRIOR REVIEW REJECTED THIS WORK — address it:")
        parts.append(str(task["review_feedback"]))
    return "\n".join(parts).strip()


def _looks_rate_limited(text: str | None) -> bool:
    """True when a child's output indicates a transient usage/rate limit (HTTP
    429) rather than a genuine task failure. Conservative ON PURPOSE — it keys
    only on the API's own rate-limit signals, so a real failure is never misread
    as one (a misread would re-queue forever instead of failing). Callers check
    this ONLY on an already-failed run, so a successful task that merely mentions
    "rate limit" in its output is unaffected.

    Signals (any one): the headless 429 envelope's structured fields
    (`"error":"rate_limit"`, `apiErrorStatus":429`) or claude's user-facing limit
    message ("You've hit your limit · resets …", "usage limit")."""
    if not text:
        return False
    t = text.lower()
    if "rate_limit" in t:
        return True
    if "apierrorstatus" in t and "429" in t:
        return True
    return "hit your limit" in t or "usage limit" in t


def _run_classic(task: dict) -> dict:
    prompt = _build_prompt(task)
    model = resolve_model(task)
    # v0.7.2: skip-permissions — the task already cleared the pre-dispatch
    # gates (risk gate / autonomy / approval), so claude runs tools without
    # the headless permission wall that otherwise silently no-ops every task.
    argv = [CLAUDE_CLI, "-p", prompt, "--dangerously-skip-permissions"]
    if model:
        argv += ["--model", model]

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_build_env(),
        cwd=_task_cwd(),
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
    _ok = proc.returncode == 0
    return {"ok": _ok, "stdout": out, "stderr": err,
            "returncode": proc.returncode,
            # v0.7.4 — flag a 429 (only on failure) so run_once re-queues it.
            "rate_limited": (not _ok)
            and (_looks_rate_limited(out) or _looks_rate_limited(err))}


def _run_stream(task: dict) -> dict:
    """Stream mode. Parses JSON lines for `session_id` + assistant text,
    scans for DECISION: / INBOX: markers, tails the follow-up mailbox."""
    prompt = _build_prompt(task)
    model = resolve_model(task)
    # v0.7.2: --verbose is REQUIRED by the CLI with -p + stream-json (it errors
    # out otherwise); skip-permissions lets the dispatched agent use tools (the
    # pre-dispatch gates are the safety layer).
    argv = [CLAUDE_CLI, "-p", prompt, "--output-format", "stream-json",
            "--verbose", "--dangerously-skip-permissions"]
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
        cwd=_task_cwd(),
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
    rate_limited = False  # v0.7.4 — set on a 429 envelope; gates re-queue
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

            # v0.7.4 — a usage/rate limit (429) surfaces as a structured error
            # envelope; flag it so a failed run is re-queued (retryable) not failed.
            if obj.get("error") == "rate_limit" or obj.get("apiErrorStatus") == 429:
                rate_limited = True

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
        ok = (rc == 0 if rc is not None else True)
        # v0.7.4 — only a FAILED run can be rate-limited (a successful task that
        # merely mentions "rate limit" must not be re-queued).
        if not ok and not rate_limited:
            rate_limited = _looks_rate_limited(out_tail)
        return {"ok": ok, "stdout": out_tail, "stderr": "",
                "returncode": rc, "session_id": session_id,
                "rate_limited": (not ok) and rate_limited}
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        _unmark_child_pid(proc.pid)
        marker.unlink(missing_ok=True)


# -- Adversarial review gate (v0.7.0) -----------------------------------
#
# Ported from cc-sdd's /kiro-impl Implementer+Reviewer pattern (Storm Bear
# wiki Pattern #76). After a review_mode skill's implementer exits ok, an
# INDEPENDENT reviewer `claude -p` child re-reads the workspace (same cwd +
# env, so it sees the files the implementer touched — fresh evidence, not
# self-report) and emits a verdict that gates completion.

# Case-sensitive verdict tokens (spec step 4). The separator after the token
# may be an em-dash, a single hyphen, or a double hyphen — or absent (VERIFIED
# carries no reason). The reason is the trailing free text.
_REVIEW_VERDICT_RE = re.compile(
    r"^\s*VERDICT:\s*(VERIFIED|NOT_VERIFIED|MANUAL_VERIFY_REQUIRED)\s*(?:—|-{1,2})?\s*(.*)$"
)


def _parse_review_verdict(text: str) -> dict | None:
    """Scan `text` for the LAST line matching the VERDICT marker.

    Returns {"verdict": str, "reason": str | None}, or None when no line
    matches (the caller applies the fail-safe). Last-match-wins so a stray
    earlier "VERDICT:" inside the reviewer's reasoning can't beat the final
    line. Pure function — unit-smokeable without a subprocess."""
    found: dict | None = None
    for line in (text or "").splitlines():
        m = _REVIEW_VERDICT_RE.match(line)
        if m:
            reason = (m.group(2) or "").strip() or None
            found = {"verdict": m.group(1), "reason": reason}
    return found


def _build_review_prompt(task: dict, impl_result: dict) -> str:
    """Compose the reviewer prompt. success_criteria (when set) appears
    verbatim; absent → "(none specified — judge against INTENT)". Reuses the
    same summary_head slice as run_once (last 20 stdout lines) for the
    implementer-output tail. Pure — stop-condition 9 unit-smokes it."""
    out_lines = (impl_result.get("stdout") or "").strip().splitlines()
    impl_tail = "\n".join(out_lines[-20:]) if out_lines else "(no output captured)"
    criteria = task.get("success_criteria")
    criteria_text = (
        str(criteria) if (criteria and str(criteria).strip())
        else "(none specified — judge against INTENT)"
    )
    return "\n".join([
        "You are an INDEPENDENT reviewer. Do NOT trust the implementer's",
        "self-report — verify against the actual workspace.",
        "",
        f"TASK: {task.get('title') or '(untitled)'}",
        f"INTENT: {task.get('description') or '(none)'}",
        f"SUCCESS CRITERIA: {criteria_text}",
        "",
        "IMPLEMENTER OUTPUT (tail of its run):",
        impl_tail,
        "",
        "Independently check whether the intent + criteria were ACTUALLY met.",
        "Re-read any files involved. Be skeptical: look for claimed-but-absent",
        "work, partial completion, hallucinated success, unaddressed criteria.",
        "",
        "End with EXACTLY one line, nothing after it:",
        "VERDICT: VERIFIED",
        "VERDICT: NOT_VERIFIED — <one-line reason>",
        "VERDICT: MANUAL_VERIFY_REQUIRED — <one-line reason>",
    ])


def _run_review(task: dict, impl_result: dict) -> dict:
    """A second `claude -p` child that independently verifies the work.

    Same blocking shape as _run_classic (stdin DEVNULL, communicate + timeout,
    start_new_session, PID-marked mode="review" so the sweep + emergency-stop
    argv check cover it). Runs in the SAME cwd + env via _build_env() so it can
    re-read the implementer's files.

    Returns {"verdict": str, "reason": str | None, "cost_usd": float | None}.

    FAIL-SAFE (Rule 12 / Rule 2 — never silently complete): a timeout, a
    non-zero exit, or NO parseable VERDICT line all return
    MANUAL_VERIFY_REQUIRED. An unverifiable review escalates to a human; it
    never auto-completes.

    DEVIATION FROM SPEC, documented: the spec says "same shape as _run_classic"
    (plain stdout) and "_run_review returns {verdict, reason}". Plain `claude
    -p` emits no cost, but stop condition 10 requires the reviewer's spend to
    count toward the per-skill budget + global cap (a reviewed task moves
    today_cost_usd by ~2×). So the reviewer runs with `--output-format json`
    (the same family as _run_stream's stream-json), the VERDICT scan runs on
    the parsed `result` text (spec step 4), and total_cost_usd is captured +
    returned so the gate can attribute it. If the payload isn't the expected
    JSON, it falls back to scanning raw stdout, then to the fail-safe.

    CC_REVIEW_MODEL points review at a cheaper tier; unset → resolve_model(task)
    (the implementer's model). No cost pre-gating — post-hoc, like the budget."""
    prompt = _build_review_prompt(task, impl_result)
    model = os.environ.get("CC_REVIEW_MODEL") or resolve_model(task)
    # v0.7.2: skip-permissions so the reviewer can freely re-read the workspace
    # (fresh-evidence check). --output-format json (not stream-json) needs no
    # --verbose.
    argv = [CLAUDE_CLI, "-p", prompt, "--output-format", "json",
            "--dangerously-skip-permissions"]
    if model:
        argv += ["--model", model]

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_build_env(),
        cwd=_task_cwd(),
        start_new_session=True,
    )
    marker = _mark_child_pid(proc.pid, task["id"], "review")
    out = ""
    try:
        out, _err = proc.communicate(timeout=TASK_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {"verdict": "MANUAL_VERIFY_REQUIRED",
                "reason": "reviewer timed out", "cost_usd": None}
    finally:
        _unmark_child_pid(proc.pid)
        marker.unlink(missing_ok=True)

    if proc.returncode != 0:
        # v0.7.4 — a 429 here is transient; flag it so the task is re-queued
        # (retryable) rather than escalated to a human as "unverifiable".
        return {"verdict": "MANUAL_VERIFY_REQUIRED",
                "reason": f"reviewer exited rc={proc.returncode}",
                "cost_usd": None,
                "rate_limited": _looks_rate_limited(out)}

    # Extract the assistant text (for the VERDICT scan) + total_cost_usd from
    # the json result envelope; fall back to raw stdout on a parse miss.
    review_text = out
    cost_usd: float | None = None
    try:
        payload = json.loads(out)
        if isinstance(payload, dict):
            review_text = payload.get("result") or out
            tc = payload.get("total_cost_usd")
            if isinstance(tc, (int, float)) and not isinstance(tc, bool):
                cost_usd = float(tc)
    except Exception:
        review_text = out

    parsed = _parse_review_verdict(review_text)
    if parsed is None:
        return {"verdict": "MANUAL_VERIFY_REQUIRED",
                "reason": "reviewer produced no parseable verdict",
                "cost_usd": cost_usd,
                "rate_limited": _looks_rate_limited(review_text)}
    return {"verdict": parsed["verdict"], "reason": parsed["reason"],
            "cost_usd": cost_usd}


# -- Orchestration ------------------------------------------------------

def _running_count() -> int:
    """How many ops_tasks are currently in 'running' state."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ops_tasks WHERE status='running'"
        ).fetchone()
        return int(row["n"]) if row else 0


def _today_cost_usd() -> float:
    """Sum of cost_usd for api_pool tasks completed today (local time).

    v0.6.0 — cap is api_pool-only. Max-sub spend is notional for Pro/Max
    operators on subsidised interactive sessions; capping on it would
    surprise users. The cap exists to protect real-dollar API spend.
    """
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS s
            FROM ops_tasks
            WHERE cost_source = 'api_pool'
              AND cost_usd IS NOT NULL
              AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
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
        "skill_budget_capped": 0,
        # v0.7.0 — adversarial review gate outcomes.
        "review_verified": 0, "review_retried": 0, "review_escalated": 0,
        # v0.7.4 — tasks re-queued because a child hit a usage/rate limit (429).
        "rate_limited": 0,
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

    # v0.6.0 — belt-and-braces. The column default covers rows created
    # after the migration; this line catches any pre-migration tasks that
    # were sitting in 'pending' during the upgrade.
    if claimed:
        ids = [t["id"] for t in claimed]
        placeholders = ",".join("?" * len(ids))
        with db.connect() as conn:
            conn.execute(
                f"UPDATE ops_tasks SET cost_source='api_pool' "
                f"WHERE id IN ({placeholders})",
                ids,
            )
        for t in claimed:
            t["cost_source"] = "api_pool"

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

        # Guard 4 (v0.6.7): per-skill daily budget. Refuse to dispatch when
        # today's api_pool spend for this skill already meets the budget. Post-
        # hoc shape — matches the global cap; operator can over-spend by at
        # most one task's cost beyond the budget. NULL budget = unlimited.
        if task.get("assigned_skill"):
            budget, today_spend = _skill_budget_state(task["assigned_skill"])
            if budget is not None and today_spend >= budget:
                task_tracker.update_task(task["id"], status="pending", started_at=None)
                stats["skill_budget_capped"] += 1
                task_tracker.log_activity(
                    "dispatcher_skill_budget_capped",
                    f"skill={task['assigned_skill']} "
                    f"today=${today_spend:.4f} budget=${budget:.2f}",
                    metadata={
                        "task_id": task["id"],
                        "skill": task["assigned_skill"],
                        "today_cost_usd": today_spend,
                        "daily_budget_usd": budget,
                    },
                )
                if verbose:
                    print(
                        f"[dispatcher] skill-budget-capped: {task['assigned_skill']} "
                        f"${today_spend:.4f} ≥ ${budget:.2f}"
                    )
                continue

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
            # v0.7.0 — adversarial review gate. review_mode is the ONLY trigger
            # (per skill); dry-run tasks skip it (nothing executed to verify).
            # The non-review path below is byte-identical to pre-v0.7.0.
            review_on = (
                not task.get("dry_run")
                and _skill_review_mode(task.get("assigned_skill"))
            )
            if not review_on:
                task_tracker.complete_task(
                    task["id"],
                    output_summary=summary_head,
                    session_id=result.get("session_id"),
                    duration_ms=elapsed_ms,
                )
                stats["succeeded"] += 1
            else:
                verdict = _run_review(task, result)
                # v0.7.4 — reviewer hit a usage/rate limit (429): transient, not
                # a real "unverifiable" result. Re-queue the task instead of
                # escalating to a human; it re-runs (impl + review) after backoff.
                if verdict.get("rate_limited"):
                    task_tracker.requeue_for_retry(
                        task["id"], RATE_LIMIT_RETRY_BACKOFF_S)
                    stats["rate_limited"] += 1
                    task_tracker.log_activity(
                        "task_rate_limited",
                        f"task_id={task['id']} reviewer usage-limit (429) "
                        f"→ re-queued",
                        metadata={"task_id": task["id"]},
                    )
                    continue
                v, reason = verdict["verdict"], verdict.get("reason")
                # Attribute the reviewer's spend to the SAME task (post-hoc; the
                # retry reuses this task id, so there is no separate row).
                # Additive so the per-skill budget (v0.6.7) + global cap
                # (v0.2.0) see BOTH children — stop condition 10.
                review_cost = verdict.get("cost_usd")
                if review_cost:
                    with db.connect() as conn:
                        conn.execute(
                            "UPDATE ops_tasks "
                            "SET cost_usd = COALESCE(cost_usd, 0) + ? WHERE id = ?",
                            (review_cost, task["id"]),
                        )
                task_tracker.update_task(task["id"], review_verdict=v,
                                         review_feedback=reason)
                task_tracker.log_activity(
                    "task_review_verdict",
                    f"task_id={task['id']} verdict={v}",
                    metadata={"task_id": task["id"], "verdict": v, "reason": reason},
                )

                if v == "VERIFIED":
                    task_tracker.complete_task(
                        task["id"],
                        output_summary=summary_head,
                        session_id=result.get("session_id"),
                        duration_ms=elapsed_ms,
                    )
                    stats["review_verified"] += 1

                elif v == "NOT_VERIFIED" and (task.get("review_count") or 0) == 0:
                    # One automatic retry. Re-queue; the next sweep re-runs with
                    # the feedback prepended (see _build_prompt). Hard cap at 1.
                    task_tracker.update_task(task["id"], status="pending",
                                             started_at=None, review_count=1)
                    stats["review_retried"] += 1

                else:
                    # NOT_VERIFIED after the retry, OR MANUAL_VERIFY_REQUIRED →
                    # human. Lands in awaiting_approval (exactly like the risk
                    # gate); resolved by the EXISTING approve/reject + Telegram
                    # /approve /cancel. No new decision wiring.
                    #
                    # v0.7.1 — PRESERVE the implementer's output at escalation.
                    # Pre-v0.7.1 this branch discarded summary_head (only the
                    # VERIFIED arm above calls complete_task), so an escalated
                    # task had output_summary=NULL and the v0.7.1 /accept action
                    # would have nothing to accept. Store the same tail +
                    # session_id + duration the VERIFIED arm keeps, so the
                    # operator can read it on the card and accept it as-is.
                    task_tracker.update_task(
                        task["id"], status="awaiting_approval", started_at=None,
                        output_summary=summary_head,
                        session_id=result.get("session_id"),
                        duration_ms=elapsed_ms,
                    )
                    stats["review_escalated"] += 1
                    task_tracker.log_activity(
                        "task_review_escalated",
                        f"task_id={task['id']} verdict={v} reason={reason}",
                        metadata={"task_id": task["id"], "verdict": v},
                    )
        elif result.get("rate_limited"):
            # v0.7.4 — transient usage/rate limit (429), NOT a real failure.
            # Re-queue with a backoff (claim_pending honours scheduled_for) so
            # the task runs again once quota resets — without burning it or
            # bumping consecutive_failures.
            task_tracker.requeue_for_retry(task["id"], RATE_LIMIT_RETRY_BACKOFF_S)
            stats["rate_limited"] += 1
            task_tracker.log_activity(
                "task_rate_limited",
                f"task_id={task['id']} usage-limit (429) → re-queued, "
                f"retry in ~{RATE_LIMIT_RETRY_BACKOFF_S}s",
                metadata={"task_id": task["id"]},
            )
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
