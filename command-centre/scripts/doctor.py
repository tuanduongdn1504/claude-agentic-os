"""`cc doctor` — deterministic health check.

Zero LLM calls, zero outbound network (beyond pinging localhost and
optionally Telegram's `getMe`). Green/red lines, exit 0 iff every
critical check passes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# ---------- styling ----------
_IS_TTY = sys.stdout.isatty()
def _c(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _IS_TTY else s
def ok(s: str) -> str:   return _c(s, "32;1")
def warn(s: str) -> str: return _c(s, "33;1")
def err(s: str) -> str:  return _c(s, "31;1")
def dim(s: str) -> str:  return _c(s, "2")


@dataclass
class Check:
    name: str
    run: Callable[[], "Result"]
    critical: bool = True


@dataclass
class Result:
    kind: str       # 'ok' | 'warn' | 'error' | 'skip'
    detail: str = ""


# ---------- individual checks ----------

def chk_python() -> Result:
    v = sys.version_info
    msg = f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if v < (3, 9):
        return Result("error", msg + " · need ≥ 3.9")
    if v < (3, 10):
        return Result("warn", msg + " · 3.10+ recommended (some modules use union syntax)")
    return Result("ok", msg)


def chk_claude_cli() -> Result:
    p = shutil.which("claude")
    if not p:
        return Result("warn", "`claude` CLI not on PATH — dispatcher will fail to spawn tasks")
    return Result("ok", f"claude → {p}")


def chk_settings_json(port: int) -> Result:
    path = Path(os.environ.get("CLAUDE_SETTINGS_PATH") or
                os.path.expanduser("~/.claude/settings.json"))
    if not path.exists():
        return Result("warn", f"{path} missing — run `cc setup otel`")
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return Result("error", f"{path} unparseable: {exc}")
    env = data.get("env") or {}
    required = {
        "CLAUDE_CODE_ENABLE_TELEMETRY", "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER", "OTEL_LOG_TOOL_DETAILS",
    }
    missing = sorted(required - env.keys())
    if missing:
        return Result("warn", f"{path} missing keys: {', '.join(missing)} — run `cc setup otel`")
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if str(port) not in endpoint:
        return Result("warn", f"endpoint {endpoint!r} does not match dashboard port {port}")
    return Result("ok", f"{path} · 6/6 OTEL keys present")


def chk_claude_projects() -> Result:
    root = Path(os.path.expanduser("~/.claude/projects"))
    if not root.exists():
        return Result("warn", f"{root} missing — nothing for sync_sessions to scrape yet")
    count = sum(1 for _ in root.rglob("*.jsonl"))
    return Result("ok", f"{root} · {count} .jsonl files")


def chk_cc_project_root() -> Result:
    v = os.environ.get("CC_PROJECT_ROOT")
    if not v:
        return Result("warn", "CC_PROJECT_ROOT unset — dispatcher will use install dir as cwd")
    p = Path(v)
    if not p.exists():
        return Result("warn", f"CC_PROJECT_ROOT={v} does not exist")
    return Result("ok", f"CC_PROJECT_ROOT={v}")


def chk_port_open(host: str, port: int) -> Result:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        s.close()
        return Result("ok", f"{host}:{port} reachable")
    except Exception as exc:
        return Result("error", f"{host}:{port} unreachable ({exc})")


def chk_system_health(base: str) -> Result:
    try:
        with urllib.request.urlopen(f"{base}/api/system/health", timeout=3) as r:
            data = json.loads(r.read())
    except Exception as exc:
        return Result("error", f"GET /api/system/health failed: {exc}")

    def _age(s: float | int | None) -> str:
        if s is None:
            return "never"
        return f"{int(s)}s"

    parts = [
        f"uptime={_age(data.get('uptime_s'))}",
        f"mem={data.get('mem_rss_mb')}MB",
        f"otel={_age(data.get('last_otel_event_age_s'))}",
        f"sync={_age(data.get('sync_loop_heartbeat_age_s'))}",
        f"daemon={_age(data.get('daemon_last_tick_age_s'))}",
        f"tz={data.get('tz')}",
    ]
    # Warn if any of OTEL/daemon/sync ages exceed a sane bound.
    warns = []
    sync = data.get("sync_loop_heartbeat_age_s")
    if sync and sync > 600:
        warns.append(f"sync {sync:.0f}s > 10min")
    daemon = data.get("daemon_last_tick_age_s")
    if daemon is not None and daemon > 600:
        warns.append(f"daemon {daemon:.0f}s > 10min")
    msg = " · ".join(parts)
    if warns:
        return Result("warn", f"{msg} · " + "; ".join(warns))
    return Result("ok", msg)


def chk_launchctl(label: str) -> Result:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=3)
    except Exception:
        return Result("skip", "launchctl unavailable")
    if label in out.stdout:
        # Extract the PID/exit columns if present.
        for line in out.stdout.splitlines():
            if label in line:
                return Result("ok", f"{label} · {line.strip()}")
        return Result("ok", label)
    return Result("warn", f"{label} not loaded (run `launchctl load` via install.sh)")


def chk_telegram() -> Result:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        return Result("skip", "TELEGRAM_BOT_TOKEN unset — telegram disabled")
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getMe", timeout=3) as r:
            data = json.loads(r.read())
    except Exception as exc:
        return Result("error", f"telegram getMe failed: {exc}")
    if data.get("ok"):
        u = data.get("result", {})
        return Result("ok", f"telegram bot @{u.get('username','?')} ({u.get('id','?')})")
    return Result("error", f"telegram getMe returned {data}")


# ---------- runner ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("CC_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CC_PORT", "8765")))
    args = ap.parse_args(argv)
    base = f"http://{args.host}:{args.port}"

    checks: list[Check] = [
        Check("python",           chk_python, critical=True),
        Check("claude cli",       chk_claude_cli, critical=False),
        Check("settings.json",    lambda: chk_settings_json(args.port), critical=False),
        Check("~/.claude/projects", chk_claude_projects, critical=False),
        Check("CC_PROJECT_ROOT",  chk_cc_project_root, critical=False),
        Check("dashboard port",   lambda: chk_port_open(args.host, args.port), critical=True),
        Check("system health",    lambda: chk_system_health(base), critical=True),
        Check("launchd · mc",     lambda: chk_launchctl("com.commandcentre.mission-control"), critical=False),
        Check("launchd · telegram", lambda: chk_launchctl("com.commandcentre.telegram-bot"), critical=False),
        Check("telegram",         chk_telegram, critical=False),
    ]

    print(f"doctor · {base}\n")
    critical_fail = 0
    for c in checks:
        try:
            r = c.run()
        except Exception as exc:
            r = Result("error", f"check crashed: {exc!r}")
        label = {
            "ok": ok("✓"), "warn": warn("⚠"),
            "error": err("✗"), "skip": dim("·"),
        }[r.kind]
        print(f"  {label} {c.name:<22} {r.detail}")
        if r.kind == "error" and c.critical:
            critical_fail += 1

    print()
    if critical_fail:
        print(err(f"{critical_fail} critical check(s) failed."))
        return 1
    print(ok("all critical checks passed."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
