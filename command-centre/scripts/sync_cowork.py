"""Cowork JSONL scraper — stub.

Cowork sessions live at:
  ~/Library/Application Support/Claude/local-agent-mode-sessions/**/audit.jsonl

Delegates JSONL parsing to sync_sessions.py by reusing the same event
grammar (user/assistant/tool_use/tool_result are the same shapes). The
only difference is the `source` column becomes 'cowork'.

For now this just probes the directory and invokes the shared parser
through a thin adapter. A future version will add Cowork-specific bits
like plugin-scoped skills.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_sessions as ss  # noqa: E402

COWORK_ROOT = Path(os.environ.get("CC_COWORK_DIR") or
                   os.path.expanduser("~/Library/Application Support/Claude/local-agent-mode-sessions"))


def run_sync(full: bool = False, verbose: bool = False) -> dict[str, int]:
    """Sync Cowork. Silent no-op if directory is missing."""
    stats = {"files_scanned": 0, "files_parsed": 0, "sessions_upserted": 0}
    if not COWORK_ROOT.exists():
        if verbose:
            print(f"cowork: {COWORK_ROOT} missing, skipping")
        return stats

    # Temporarily point the shared parser at COWORK_ROOT. sync_sessions uses
    # module-level CLAUDE_PROJECTS; we swap it for one call.
    prev = ss.CLAUDE_PROJECTS
    try:
        ss.CLAUDE_PROJECTS = COWORK_ROOT
        stats = ss.run_sync(full=full, verbose=verbose)
    finally:
        ss.CLAUDE_PROJECTS = prev
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    stats = run_sync(full=args.full, verbose=True)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
