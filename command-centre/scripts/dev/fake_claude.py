#!/usr/bin/python3
"""Dev-only stand-in for `claude -p`.

Used in dispatcher smoke tests where we don't want to burn real API
tokens. Set `CLAUDE_CLI_OVERRIDE` to this file's absolute path before
running the dispatcher.

Emits a small, valid-looking stream-JSON transcript for stream mode, or
plain text for classic mode. Supports:

  --model <m>            (ignored)
  --output-format stream-json      enables stream mode

If the prompt contains the word "DECISION", a DECISION: line is emitted
(useful for HITL testing). If it contains "INBOX", an INBOX: line is
emitted. Reads any follow-up lines from stdin and echoes them back.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid


def stream_json_out(session_id: str, lines: list[str]) -> None:
    # Initial init envelope so the dispatcher learns the session_id.
    sys.stdout.write(json.dumps({
        "type": "system", "subtype": "init",
        "session_id": session_id,
    }) + "\n")
    sys.stdout.flush()
    for text in lines:
        sys.stdout.write(json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": text}],
            },
        }) + "\n")
        sys.stdout.flush()
        time.sleep(0.05)


def maybe_read_stdin(deadline_s: float) -> list[str]:
    """Non-blocking read of any follow-ups that arrive before deadline."""
    import selectors
    sel = selectors.DefaultSelector()
    sel.register(sys.stdin, selectors.EVENT_READ)
    out: list[str] = []
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        events = sel.select(timeout=0.2)
        if not events:
            continue
        line = sys.stdin.readline()
        if not line:
            break
        out.append(line.strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--prompt", dest="prompt")
    ap.add_argument("--model", default="claude-opus-4-7")
    ap.add_argument("--output-format", dest="fmt", default="text")
    args, _ = ap.parse_known_args()

    # Positional fallback: `claude -p "the prompt"` sometimes passes the
    # prompt as a positional after the flag parser.
    prompt = args.prompt or " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
    prompt = prompt or ""

    session_id = uuid.uuid4().hex

    if args.fmt == "stream-json":
        # Phase 1: init + first assistant message.
        stream_json_out(session_id, [
            "Hello from fake-claude. I am going to think about this briefly.\n",
        ])

        # Phase 2: optional DECISION: prompt, wait for answer.
        if "DECISION" in (prompt or "").upper() or os.environ.get("FAKE_CLAUDE_DECISION"):
            stream_json_out(session_id, [
                "I need a check:\nDECISION: Should I proceed with the risky step?",
            ])
            # Block until we get an answer line on stdin (or timeout).
            replies = maybe_read_stdin(30.0)
            if replies:
                stream_json_out(session_id, [
                    f"Got the answer: {replies[0]!r}. Continuing.\n",
                ])

        # Phase 3: optional INBOX message.
        if "INBOX" in (prompt or "").upper() or os.environ.get("FAKE_CLAUDE_INBOX"):
            stream_json_out(session_id, [
                "INBOX: Heads-up — the remote server returned a 502 on the first try, retrying.",
            ])

        # Phase 4: final result envelope with a fake cost.
        sys.stdout.write(json.dumps({
            "type": "result", "session_id": session_id,
            "total_cost_usd": 0.01234, "duration_ms": 321,
            "is_error": False, "stop_reason": "end_turn",
        }) + "\n")
        sys.stdout.flush()
        return 0

    # Classic mode — plain text, no stream markers.
    sys.stdout.write(
        f"[fake-claude] session={session_id}\n"
        f"prompt: {prompt[:140]}...\n"
        "did the thing.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
