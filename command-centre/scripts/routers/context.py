"""/api/context/health — non-LLM scan of ~/.claude/settings.json and CLAUDE.md.

Reports line count, rule count (numbered-list items in CLAUDE.md),
MCP-server count, hook count, file size. Used by the UI's ContextHealth
card. Zero LLM calls.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()


def _file_stats(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        txt = path.read_text(errors="replace")
    except Exception as exc:
        return {"exists": False, "error": str(exc)}
    lines = txt.count("\n") + (0 if txt.endswith("\n") else 1 if txt else 0)
    return {
        "exists": True,
        "path": str(path),
        "bytes": st.st_size,
        "lines": lines,
        "mtime_ts": st.st_mtime,
    }


_NUMBERED = re.compile(r"^\s*(?:\d+[\.\)]|[-*])\s+", re.MULTILINE)


def _count_rules(txt: str) -> int:
    return len(_NUMBERED.findall(txt))


@router.get("/api/context/health")
async def context_health() -> dict[str, Any]:
    settings_path = Path(os.environ.get("CLAUDE_SETTINGS_PATH") or
                         os.path.expanduser("~/.claude/settings.json"))
    claude_md_path = Path(os.environ.get("CLAUDE_MD_PATH") or
                          os.path.expanduser("~/.claude/CLAUDE.md"))

    # Settings: count env keys, MCP servers, hooks.
    settings_info: dict[str, Any] = _file_stats(settings_path)
    if settings_info.get("exists"):
        try:
            data = json.loads(settings_path.read_text())
        except Exception:
            data = {}
        settings_info["env_keys"] = len(data.get("env") or {})
        mcp_servers = data.get("mcpServers") or data.get("mcp_servers") or {}
        settings_info["mcp_server_count"] = len(mcp_servers) if isinstance(mcp_servers, dict) else 0
        hooks = data.get("hooks") or {}
        # hooks is a dict whose values are arrays of hook entries. Count entries.
        total = 0
        if isinstance(hooks, dict):
            for v in hooks.values():
                if isinstance(v, list):
                    total += sum(1 for _ in v)
        settings_info["hook_count"] = total

    # CLAUDE.md stats.
    md_info = _file_stats(claude_md_path)
    if md_info.get("exists"):
        try:
            txt = claude_md_path.read_text(errors="replace")
            md_info["rule_count"] = _count_rules(txt)
        except Exception:
            md_info["rule_count"] = None

    return {"settings": settings_info, "claude_md": md_info}
