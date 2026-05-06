"""Claude Code hook entry point.

Wire via `~/.claude/settings.json`:

  {
    "hooks": {
      "SessionStart": [
        { "matcher": "", "hooks": [ { "type": "command", "command": "/usr/bin/python3 <install>/.claude/skills/mission-control/scripts/session_state_hook.py" } ] }
      ],
      "PreToolUse":  [{ "matcher": "", "hooks": [{ "type": "command", "command": "..." }] }],
      "PostToolUse": [{ "matcher": "", "hooks": [{ "type": "command", "command": "..." }] }],
      "Stop":        [{ "matcher": "", "hooks": [{ "type": "command", "command": "..." }] }]
    }
  }

Claude Code passes JSON on stdin. We write a single upsert into
`live_session_state` per invocation — sub-millisecond — so the UI's
LiveSessions drawer has current tool + state.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(os.environ.get("CC_INSTALL_DIR") or
                (Path(__file__).resolve().parents[4])) / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import db  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Claude Code will complain if hooks fail — but it's not our job
        # to block the session. Exit 0 silently.
        return 0

    session_id = payload.get("session_id") or payload.get("sessionId")
    if not session_id:
        return 0

    hook_kind = payload.get("hook_event_name") or payload.get("hook") or "unknown"
    tool_name = None
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, dict):
        tool_name = payload.get("tool_name") or tool_input.get("tool_name")

    # Derive a coarse state.
    state = {
        "SessionStart": "idle",
        "PreToolUse":   "tool",
        "PostToolUse":  "idle",
        "Stop":         "stopped",
    }.get(hook_kind, "active")

    try:
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO live_session_state(session_id, state, current_tool, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    state=excluded.state,
                    current_tool=excluded.current_tool,
                    updated_at=datetime('now')
                """,
                (session_id, state, tool_name),
            )
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
