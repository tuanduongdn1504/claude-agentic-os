"""Account-tagging helpers.

Multi-account workflow: one machine runs Claude Code under several OAuth
accounts (e.g. Desktop = work account, VS Code = personal account). The
JSONL files don't carry the OAuth account ID, so we tag sessions two ways:

  1) Entrypoint PROXY — `entrypoint='claude-desktop' → account_a`, etc.
     Lossy but works retroactively on existing data. Configured in
     ~/.command-centre/data/accounts.json.

  2) Hook OVERRIDE — a SessionStart hook writes a sidecar
     `data/account-hints/<session_id>.json` with the actual
     `oauthAccount.accountUuid` captured at session start. When present,
     this beats the proxy.

Both join in `account_id_for()` below — hint > uuid mapping > entrypoint.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import db

_CONFIG_PATH = db.INSTALL_DIR / "data" / "accounts.json"
_HINTS_DIR = db.INSTALL_DIR / "data" / "account-hints"


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {"accounts": {}, "fallback": "unknown"}
    try:
        with _CONFIG_PATH.open("r") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"accounts": {}, "fallback": "unknown"}


def reload_config() -> None:
    """Force a re-read of accounts.json on next call (config file edits)."""
    _load_config.cache_clear()


def _read_hint(session_id: str) -> Optional[str]:
    """Return oauthAccount.accountUuid for this session if a hook recorded it."""
    if not session_id:
        return None
    hint_path = _HINTS_DIR / f"{session_id}.json"
    if not hint_path.exists():
        return None
    try:
        with hint_path.open("r") as fh:
            data = json.load(fh)
        uuid = data.get("account_uuid") or data.get("accountUuid")
        return uuid if isinstance(uuid, str) else None
    except (json.JSONDecodeError, OSError):
        return None


def account_id_for(
    *,
    entrypoint: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve account_id using hook hint first, then config-based mapping.

    Returns one of the keys in accounts.json (e.g. 'account_a') or the
    fallback (default 'unknown'). Returns None if no rule matches and
    no fallback is configured.
    """
    cfg = _load_config()
    accounts: dict[str, Any] = cfg.get("accounts", {}) or {}

    # 1) Hook hint (authoritative).
    uuid = _read_hint(session_id) if session_id else None
    if uuid:
        for aid, spec in accounts.items():
            if uuid in (spec.get("uuids") or []):
                return aid
        # Hint exists but unmapped — fall through to proxy so we don't lose tagging.

    # 2) Entrypoint proxy.
    if entrypoint:
        for aid, spec in accounts.items():
            if entrypoint in (spec.get("entrypoints") or []):
                return aid

    return cfg.get("fallback", "unknown")


def all_account_ids() -> list[str]:
    """Configured account keys (for join validation / UI rendering)."""
    cfg = _load_config()
    return list((cfg.get("accounts") or {}).keys())


def label_for(account_id: str) -> str:
    """Human label for an account_id; falls back to the id itself."""
    cfg = _load_config()
    spec = (cfg.get("accounts") or {}).get(account_id) or {}
    return spec.get("label") or account_id
