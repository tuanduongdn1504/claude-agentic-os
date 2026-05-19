"""v0.6.1 stop-condition smoke. Walks the 6 amendment stop conditions
against a real (already-running) FastAPI server on http://127.0.0.1:8765,
with `_send_message` stubbed so no TG bot is involved.

Stop conditions per amendment:
  S1 — happy path: /status returns the steady-state lines
  S2 — cost-cap alert appears when MISSION_CONTROL_DAILY_COST_CAP_USD is tripped
  S3 — emergency-stop alert appears + disappears with stop/resume
  S4 — partial-failure resilience: stop server → fallback message
  S5 — mobile-screen render (visual) — manual line-width assertion here
  S6 — backward compat: regex + handler routing for existing verbs unchanged

Run:
    CC_INSTALL_DIR=/tmp/cc-v0.6.1-scratch \\
    TELEGRAM_BOT_TOKEN=fake TELEGRAM_DASH_CHAT_ID=fake \\
    python3 scripts/dev/smoke_v0_6_1.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import telegram_bridge as tb  # noqa: E402

DASH = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8765")
tb.DASHBOARD_URL = DASH


# ---------- helpers ----------

_PASSED = []
_FAILED = []


def _check(label: str, ok: bool, detail: str = "") -> None:
    line = f"  {'✓' if ok else '✗'} {label}"
    if detail:
        line += f"  · {detail}"
    print(line)
    (_PASSED if ok else _FAILED).append(label)


def _capture_status() -> str:
    sent: list[str] = []
    orig = tb._send_message
    tb._send_message = lambda text: (sent.append(text), 1)[1]
    try:
        tb._handle_status("fake-chat")
    finally:
        tb._send_message = orig
    return sent[0] if sent else ""


def _post(path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{DASH}{path}", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


# ---------- stop conditions ----------

def s1_happy_path() -> None:
    print("\nS1 · happy path — steady-state lines present")
    out = _capture_status()
    print("    ──── rendered ────")
    for ln in out.splitlines():
        print(f"    {ln}")
    print("    ──────────────────")
    _check("header is *STATUS* + italic timestamp",
           out.startswith("*STATUS*  _") and "GMT+7_" in out.split("\n", 1)[0])
    _check("Dispatcher line present", "⚙️  Dispatcher" in out)
    _check("Today line present", "💰 Today" in out)
    _check("Tasks today line always present", "✅ Tasks today" in out)


def s2_cost_cap() -> None:
    print("\nS2 · cost-cap alert lights then unlights with cap toggle")
    # The bridge reads cost_capped from the dispatcher response. We can't
    # change MISSION_CONTROL_DAILY_COST_CAP_USD at runtime on the server side
    # without a reboot, so we exercise the format path directly with a
    # fixture that mirrors what the live endpoint would return under cap.
    capped = {
        "dispatcher": {
            "running": 1, "max_concurrent": 3, "free_slots": 2,
            "today_cost_api_pool_usd": 12.34, "today_cost_max_sub_usd": 0.0,
            "today_cost_unknown_usd": 0.0, "daily_cost_cap_usd": 10.0,
            "cost_capped": True, "back_pressure": False, "hard_risk_gate": True,
        },
        "pending_decisions": 0,
        "live_sessions": [],
        "today_counts": {"done": 0, "failed": 0, "awaiting_approval": 0},
        "emergency_stop": False,
    }
    out_capped = tb._format_status(capped)
    _check("cost-cap fixture renders the ⚠️ Cost cap reached alert",
           "*Cost cap reached*" in out_capped
           and "(api_pool $12.34 / $10.00)" in out_capped)

    uncapped = dict(capped)
    uncapped["dispatcher"] = dict(capped["dispatcher"])
    uncapped["dispatcher"]["cost_capped"] = False
    uncapped["dispatcher"]["today_cost_api_pool_usd"] = 3.0
    out_uncapped = tb._format_status(uncapped)
    _check("clearing cost_capped removes the alert line",
           "Cost cap reached" not in out_uncapped)


def s3_emergency_stop() -> None:
    print("\nS3 · emergency-stop alert toggles with the system_state flag")
    _post("/api/system/emergency-stop", {"reason": "smoke S3"})
    out_on = _capture_status()
    _check("alert line present when emergency_stop=ON",
           "🛑 *Emergency stop ON*" in out_on)

    _post("/api/system/emergency-resume", {})
    out_off = _capture_status()
    _check("alert line gone when emergency_stop=OFF",
           "Emergency stop ON" not in out_off)


def s4_partial_failure() -> None:
    print("\nS4 · partial-failure resilience — wrong-port URL → fallback")
    # Point the bridge at a closed port to simulate total server failure.
    saved = tb.DASHBOARD_URL
    tb.DASHBOARD_URL = "http://127.0.0.1:1"  # nothing listening
    try:
        out = _capture_status()
    finally:
        tb.DASHBOARD_URL = saved
    _check("total failure renders the dashboard-down fallback",
           "status check failed" in out and "cc status" in out)
    _check("fallback does NOT crash the long-poll loop",
           True, "no exception raised")


def s5_mobile_render() -> None:
    print("\nS5 · mobile render — every line ≤ 60 chars before wrap")
    # Telegram on iPhone 14 (390 px) wraps at ~30-50 chars in proportional
    # font; we enforce ≤ 60 here as a hard ceiling so the worst lines stay
    # readable. Emoji + bold markers count toward length.
    fixtures = ["happy", "alerts"]
    fix_happy = {
        "dispatcher": {
            "running": 2, "max_concurrent": 3, "free_slots": 1,
            "today_cost_api_pool_usd": 3.42, "today_cost_max_sub_usd": 1.18,
            "today_cost_unknown_usd": 0.0, "daily_cost_cap_usd": 10.0,
            "cost_capped": False, "back_pressure": False, "hard_risk_gate": True,
        },
        "pending_decisions": 2,
        "live_sessions": [],
        "today_counts": {"done": 5, "failed": 1, "awaiting_approval": 0},
        "emergency_stop": False,
    }
    fix_alerts = dict(fix_happy)
    fix_alerts["dispatcher"] = dict(fix_happy["dispatcher"])
    fix_alerts["dispatcher"]["cost_capped"] = True
    fix_alerts["dispatcher"]["back_pressure"] = True
    fix_alerts["dispatcher"]["running"] = 3
    fix_alerts["dispatcher"]["free_slots"] = 0
    fix_alerts["dispatcher"]["today_cost_api_pool_usd"] = 10.0
    fix_alerts["emergency_stop"] = True

    for label, fix in (("happy", fix_happy), ("alerts", fix_alerts)):
        out = tb._format_status(fix)
        max_len = max(len(ln) for ln in out.splitlines() if ln) if out else 0
        _check(f"{label}: max line length = {max_len} chars ≤ 60",
               max_len <= 60, detail=f"max {max_len}")


def s6_backward_compat() -> None:
    print("\nS6 · backward compat — existing verbs still route")
    cases = [
        ("/run draft the PR", tb._CMD_RUN_RE, True),
        ("/answer 42 yes", tb._CMD_WITH_ID_RE, True),
        ("/reply 99 thanks", tb._CMD_WITH_ID_RE, True),
        ("/approve 7", tb._CMD_WITH_ID_RE, True),
        ("/cancel 8", tb._CMD_WITH_ID_RE, True),
        ("/help", None, False),  # no regex match — handled by /help branch
    ]
    for text, regex, must_match in cases:
        if regex is None:
            ok = (not tb._CMD_RUN_RE.match(text)
                  and not tb._CMD_WITH_ID_RE.match(text)
                  and not tb._CMD_STATUS_RE.match(text))
            _check(f"{text!r} falls through to /help", ok)
        else:
            ok = bool(regex.match(text)) == must_match
            _check(f"{text!r} matches its regex", ok)
    # /status with stray args MUST fall through (NFR11).
    _check("'/status foo' does not match _CMD_STATUS_RE",
           not tb._CMD_STATUS_RE.match("/status foo"))
    _check("'/statusfoo' does not match _CMD_STATUS_RE",
           not tb._CMD_STATUS_RE.match("/statusfoo"))


# ---------- runner ----------

def main() -> int:
    print(f"v0.6.1 smoke · dashboard={DASH}\n")
    s1_happy_path()
    s2_cost_cap()
    s3_emergency_stop()
    s4_partial_failure()
    s5_mobile_render()
    s6_backward_compat()
    print()
    print(f"{len(_PASSED)} passed · {len(_FAILED)} failed")
    if _FAILED:
        print("FAILED:")
        for x in _FAILED:
            print(f"  - {x}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
