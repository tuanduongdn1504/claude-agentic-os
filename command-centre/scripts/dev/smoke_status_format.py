"""Fixture smoke for `_format_status` — no HTTP, no daemon. Verifies the
template before the handler is wired into `_handle_message`. Run:

    CC_INSTALL_DIR=/tmp/cc-v0.6.1-scratch python3 scripts/dev/smoke_status_format.py

Expected: prints 5 named fixtures end-to-end. Operator eyeballs the output
for spacing / NFR4 mobile width / conditional-line presence. No assertions —
this is a render preview, not a pytest suite."""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Make the bridge importable without booting its loops.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

# `_load_env_file()` reads $CC_INSTALL_DIR/.env but never crashes; safe to
# import the bridge module at module-load even with the scratch dir.
import telegram_bridge as tb  # noqa: E402

_UTC = timezone.utc


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fixture_happy() -> dict:
    return {
        "dispatcher": {
            "running": 2,
            "max_concurrent": 3,
            "free_slots": 1,
            "today_cost_api_pool_usd": 3.42,
            "today_cost_max_sub_usd": 1.18,
            "today_cost_unknown_usd": 0.0,
            "daily_cost_cap_usd": 10.0,
            "cost_capped": False,
            "back_pressure": False,
            "hard_risk_gate": True,
        },
        "pending_decisions": 2,
        "live_sessions": [
            {"started_at": _utc_iso(datetime.now(_UTC) - timedelta(minutes=4))},
        ],
        "today_counts": {"done": 5, "failed": 1, "awaiting_approval": 0},
        "emergency_stop": False,
    }


def _fixture_idle() -> dict:
    return {
        "dispatcher": {
            "running": 0, "max_concurrent": 3, "free_slots": 3,
            "today_cost_api_pool_usd": 0.0, "today_cost_max_sub_usd": 0.0,
            "today_cost_unknown_usd": 0.0, "daily_cost_cap_usd": None,
            "cost_capped": False, "back_pressure": False, "hard_risk_gate": True,
        },
        "pending_decisions": 0,
        "live_sessions": [],
        "today_counts": {"done": 0, "failed": 0, "awaiting_approval": 0},
        "emergency_stop": False,
    }


def _fixture_all_alerts() -> dict:
    return {
        "dispatcher": {
            "running": 3, "max_concurrent": 3, "free_slots": 0,
            "today_cost_api_pool_usd": 10.0, "today_cost_max_sub_usd": 2.5,
            "today_cost_unknown_usd": 0.0, "daily_cost_cap_usd": 10.0,
            "cost_capped": True, "back_pressure": True, "hard_risk_gate": True,
        },
        "pending_decisions": 1,
        "live_sessions": [
            {"started_at": _utc_iso(datetime.now(_UTC) - timedelta(hours=1, minutes=23))},
            {"started_at": _utc_iso(datetime.now(_UTC) - timedelta(minutes=2))},
        ],
        "today_counts": {"done": 12, "failed": 3, "awaiting_approval": 2},
        "emergency_stop": True,
    }


def _fixture_partial() -> dict:
    return {
        "dispatcher": {
            "running": 1, "max_concurrent": 3, "free_slots": 2,
            "today_cost_api_pool_usd": 0.50, "today_cost_max_sub_usd": 0.0,
            "today_cost_unknown_usd": 0.0, "daily_cost_cap_usd": 5.0,
            "cost_capped": False, "back_pressure": False, "hard_risk_gate": True,
        },
        "pending_decisions": None,
        "live_sessions": None,
        "today_counts": {"done": 2, "failed": None, "awaiting_approval": 0},
        "emergency_stop": False,
    }


def _fixture_dispatcher_only() -> dict:
    """Only dispatcher resolved — other 4 failed."""
    return {
        "dispatcher": _fixture_happy()["dispatcher"],
        "pending_decisions": None,
        "live_sessions": None,
        "today_counts": {"done": None, "failed": None, "awaiting_approval": None},
        "emergency_stop": None,
    }


def main() -> int:
    fixtures = {
        "happy (2 sessions, 1 free, cap set)": _fixture_happy(),
        "idle (zeros, no cap)": _fixture_idle(),
        "all alerts (emergency stop + cap + back-pressure)": _fixture_all_alerts(),
        "partial (decisions + live + 1 task status missing)": _fixture_partial(),
        "dispatcher-only (4 of 5 fetches failed)": _fixture_dispatcher_only(),
    }
    for name, m in fixtures.items():
        rule = "─" * 60
        print(f"\n{rule}\n  {name}\n{rule}")
        print(tb._format_status(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
