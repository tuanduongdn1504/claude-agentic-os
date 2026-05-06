"""Interactive OTEL wizard.

Reads ~/.claude/settings.json, diffs against the 6 required keys, shows
the user what will change, backs up, merges. Never overwrites existing
user values — only adds the keys that are missing (per prompt spec).

Usage:
    setup_otel.py                       # interactive
    setup_otel.py --yes                 # no prompt, apply diff
    setup_otel.py --port 8765 --yes
    setup_otel.py --dry-run             # show diff, don't write
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


REQUIRED_KEYS_FN = lambda port: {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_EXPORTER_OTLP_ENDPOINT":  f"http://127.0.0.1:{port}",
    "OTEL_EXPORTER_OTLP_PROTOCOL":  "http/json",
    "OTEL_METRICS_EXPORTER":        "otlp",
    "OTEL_LOGS_EXPORTER":           "otlp",
    "OTEL_LOG_TOOL_DETAILS":        "1",
}


SETTINGS_PATH_DEFAULT = Path(os.environ.get("CLAUDE_SETTINGS_PATH") or
                             os.path.expanduser("~/.claude/settings.json"))


# -- tiny ansi ----------------------------------------------------------
_IS_TTY = sys.stdout.isatty()
def _c(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _IS_TTY else s
def _ok(s: str) -> str: return _c(s, "32")
def _warn(s: str) -> str: return _c(s, "33")
def _err(s: str) -> str: return _c(s, "31")
def _dim(s: str) -> str: return _c(s, "2")


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        print(_err(f"! couldn't parse {path}: {exc}"))
        sys.exit(2)


def _diff(existing: dict, required: dict[str, str]) -> dict[str, tuple[str | None, str]]:
    """Return map: key -> (existing_value, desired_value) for every
    required key. `existing_value` is None when missing. We never flag
    keys with different existing values — we only add the missing ones."""
    env = existing.get("env") or {}
    out: dict[str, tuple[str | None, str]] = {}
    for k, v in required.items():
        out[k] = (env.get(k), v)
    return out


def _print_diff(diff: dict[str, tuple[str | None, str]]) -> tuple[int, int]:
    add = same = 0
    for k, (cur, want) in diff.items():
        if cur is None:
            print(f"  {_warn('+')} {k} = {want}")
            add += 1
        elif cur == want:
            print(f"  {_ok('✓')} {k} = {cur}")
            same += 1
        else:
            print(f"  {_dim('·')} {k} = {cur}  "
                  f"{_dim(f'(keeping yours; would be {want})')}")
            same += 1
    return add, same


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="no prompt, apply diff")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CC_PORT", "8765")))
    ap.add_argument("--path", default=str(SETTINGS_PATH_DEFAULT),
                    help="path to settings.json (default: ~/.claude/settings.json)")
    args = ap.parse_args(argv)

    settings_path = Path(args.path).expanduser()
    print(f"OTEL wizard · settings: {settings_path}")
    required = REQUIRED_KEYS_FN(args.port)
    existing = _load(settings_path)

    diff = _diff(existing, required)
    add, same = _print_diff(diff)
    print(f"  {add} to add, {same} already present/preserved")

    if add == 0:
        print(_ok("nothing to do — OTEL keys already present."))
        return 0

    if not args.yes and not args.dry_run:
        try:
            ans = input(f"apply changes to {settings_path}? [Y/n] ").strip().lower()
        except EOFError:
            ans = ""
        if ans and ans not in ("y", "yes"):
            print("aborted.")
            return 1

    if args.dry_run:
        print(_dim("--dry-run: not writing"))
        return 0

    # Back up if the file exists.
    if settings_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = settings_path.with_suffix(settings_path.suffix + f".bak.{stamp}")
        shutil.copy2(settings_path, backup)
        print(f"  backup → {backup}")
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)

    merged = dict(existing)
    env = dict(merged.get("env") or {})
    for k, (cur, want) in diff.items():
        if cur is None:
            env[k] = want
    merged["env"] = env

    settings_path.write_text(json.dumps(merged, indent=2) + "\n")
    print(_ok(f"  wrote {settings_path}"))
    print(_dim("  restart Claude Code to pick up the new env."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
