"""MCP stats + schema-measurement helpers.

Surfaces used by routers/mcp.py:

- rebuild_mcp_stats() — walks otel_events (precise, post-`OTEL_LOG_TOOL_DETAILS=1`)
  plus tool_calls (JSONL side) to populate `mcp_stats`. Per-server totals.
- measure_schemas() — stub: real implementation would invoke each MCP server
  and ask for its schema. For now we log collection attempts but skip
  network calls in v1. The rebuild path still gives real latency numbers.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402


def rebuild_mcp_stats() -> dict[str, Any]:
    """Rebuild `mcp_stats` from otel_events + tool_calls.

    Sources in priority order:
      1. otel_events rows with mcp_server_name set (precise)
      2. tool_calls where tool_name LIKE 'mcp__server__tool'
    """
    totals: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {"tools": set(), "calls": 0, "errors": 0, "sum_ms": 0}
    )
    with db.connect() as conn:
        for r in conn.execute(
            "SELECT mcp_server_name, mcp_tool_name, tool_duration_ms, tool_success, tool_error "
            "FROM otel_events WHERE mcp_server_name IS NOT NULL"
        ):
            srv = r["mcp_server_name"]
            b = totals[srv]
            if r["mcp_tool_name"]:
                b["tools"].add(r["mcp_tool_name"])
            b["calls"] += 1
            if r["tool_duration_ms"]:
                b["sum_ms"] += int(r["tool_duration_ms"])
            if r["tool_success"] == 0 or r["tool_error"]:
                b["errors"] += 1
        for r in conn.execute(
            "SELECT tool_name, duration_ms, error FROM tool_calls "
            "WHERE tool_name LIKE 'mcp\\_\\_%\\_\\_%' ESCAPE '\\'"
        ):
            parts = (r["tool_name"] or "").split("__")
            if len(parts) < 3:
                continue
            srv = parts[1]
            tool = "__".join(parts[2:])
            b = totals[srv]
            b["tools"].add(tool)
            b["calls"] += 1
            if r["duration_ms"]:
                b["sum_ms"] += int(r["duration_ms"])
            if r["error"]:
                b["errors"] += 1

        now_iso = conn.execute("SELECT datetime('now')").fetchone()[0]
        for srv, b in totals.items():
            conn.execute(
                """
                INSERT INTO mcp_stats(server, tools, total_tokens, error, measured_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(server) DO UPDATE SET
                  tools=excluded.tools, total_tokens=excluded.total_tokens,
                  error=excluded.error, measured_at=excluded.measured_at
                """,
                (srv, len(b["tools"]), b["calls"],
                 (f"errors={b['errors']}" if b["errors"] else None), now_iso),
            )

    return {"servers": len(totals)}


def measure_schemas() -> dict[str, Any]:
    """Stub: v1 doesn't actually invoke MCP servers. We just upsert a
    marker row so the UI can tell 'ran sync vs never synced'."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO activities(event_type, detail) VALUES ('mcp_measure_stub', 'noop')"
        )
    return {"measured": 0, "note": "real schema probing lands with Mission Control milestone"}


def main() -> int:
    print(json.dumps(rebuild_mcp_stats(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
