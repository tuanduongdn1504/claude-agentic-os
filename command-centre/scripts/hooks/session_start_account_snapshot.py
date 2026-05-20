#!/usr/bin/env python3
"""SessionStart hook — snapshot the OAuth account a session starts under.

Claude Code writes JSONL session logs WITHOUT stamping which OAuth account
is logged in. This hook closes that gap: when a session starts, it reads
~/.claude.json.oauthAccount and writes a sidecar file:

    ~/.command-centre/data/account-hints/<session_id>.json

containing {session_id, account_uuid, email, captured_at}. The Command
Centre sync joins this against `sessions.account_id` to override the
entrypoint-based proxy with the real account.

Designed to be hard-to-break: never blocks session start, always exits 0,
swallows every error to stderr (so it doesn't pollute Claude's transcript).

Hook input arrives as JSON on stdin per the modern Claude Code contract.
Falls back to CLAUDE_SESSION_ID env if stdin is empty.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
CLAUDE_JSON = HOME / ".claude.json"
HINTS_DIR = HOME / ".command-centre" / "data" / "account-hints"


def _read_session_id() -> str | None:
    # 1) JSON on stdin (preferred).
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
            sid = data.get("session_id") or data.get("sessionId")
            if isinstance(sid, str) and sid:
                return sid
    except Exception as exc:
        print(f"[session_start_hook] stdin parse failed: {exc!r}", file=sys.stderr)
    # 2) Env fallback.
    return os.environ.get("CLAUDE_SESSION_ID") or None


def _read_oauth_account() -> dict | None:
    try:
        with CLAUDE_JSON.open("r") as fh:
            data = json.load(fh)
        return data.get("oauthAccount") or None
    except Exception as exc:
        print(f"[session_start_hook] could not read {CLAUDE_JSON}: {exc!r}", file=sys.stderr)
        return None


def main() -> int:
    session_id = _read_session_id()
    if not session_id:
        # Nothing to do — don't write an orphan hint file.
        return 0

    oauth = _read_oauth_account() or {}
    record = {
        "session_id": session_id,
        "account_uuid": oauth.get("accountUuid"),
        "email": oauth.get("emailAddress"),
        "organization_uuid": oauth.get("organizationUuid"),
        "display_name": oauth.get("displayName"),
        "captured_at": time.time(),
    }

    try:
        HINTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = HINTS_DIR / f"{session_id}.json"
        with out_path.open("w") as fh:
            json.dump(record, fh)
    except Exception as exc:
        print(f"[session_start_hook] write failed: {exc!r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[session_start_hook] unhandled: {exc!r}", file=sys.stderr)
        sys.exit(0)  # never block session start
