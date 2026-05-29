#!/usr/bin/env python3
"""Smoke test for v0.6.8 — expose historical ranges (90d / 1y / all).

Walks stop conditions 1–6 + 9 from
`observability/(C) build-your-own-dashboard-prompt-v0.6.8-amendment.md`.
(Stop 7 + 8 are UI; stop 10 backward-compat is covered by the existing
Playwright specs. Those live in `ui/tests/e2e/v0.6.8.spec.ts`.)

Shape mirrors the v0.6.7 / v0.6.5 smoke tests:
  - Real FastAPI server on 127.0.0.1:8872, backed by a temp SQLite DB.
  - Startup sync is neutralised by pointing CC_CLAUDE_PROJECTS_DIR /
    CC_COWORK_DIR at non-existent dirs, so the temp DB holds ONLY the
    rows this test seeds (the empty-DB and window-count assertions
    depend on that).
  - timerange predicates (stop 1) + days_in_range (stop 2) are checked
    in-process — no server needed for those.

The crux of the release is that wider windows actually return older rows
that the 30-day window omits, so the bulk of this file is the
multi-month seeded fixture (incl. one >1-year-old row for stop 4).

Usage:
  python3 scripts/dev/smoke_v0_6_8.py
  Exits 0 if all stop conditions pass, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "command-centre" / "scripts"

API_PORT = 8872


# ---------------------------------------------------------------------------
# Server boot
# ---------------------------------------------------------------------------

def _wait_port(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_server(install_dir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["CC_INSTALL_DIR"] = str(install_dir)
    env["CC_HOST"] = "127.0.0.1"
    env["CC_PORT"] = str(API_PORT)
    # Neutralise the startup sync so the temp DB holds only seeded rows.
    # Both sync sources early-return when their source dir is missing.
    env["CC_CLAUDE_PROJECTS_DIR"] = str(install_dir / "no-claude-projects")
    env["CC_COWORK_DIR"] = str(install_dir / "no-cowork")
    env.pop("CC_USE_FSEVENTS", None)  # default 120s poll; nothing to find anyway
    venv_py = Path.home() / ".command-centre" / "venv" / "bin" / "python3"
    py = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.Popen(
        [py, str(SCRIPTS_DIR / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(SCRIPTS_DIR),
    )
    if not _wait_port("127.0.0.1", API_PORT):
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        raise RuntimeError(
            f"server failed to come up on :{API_PORT}\n"
            f"stdout:\n{out.decode(errors='replace')}\n"
            f"stderr:\n{err.decode(errors='replace')}"
        )
    return proc


def _open_db(install_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(install_dir / "data" / "command-centre.db", timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    marker = "PASS" if ok else "FAIL"
    print(f"  [{marker}] {name}" + (f" — {note}" if note else ""))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _api_get(path: str) -> dict:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{API_PORT}{path}", timeout=10,
    ) as r:
        return json.loads(r.read())


def _api_get_timed(path: str) -> tuple[dict, float]:
    t0 = time.perf_counter()
    body = _api_get(path)
    return body, (time.perf_counter() - t0) * 1000.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Day-offsets chosen comfortably clear of the 30 / 90 / 365-day boundaries so
# date-string comparison can't land an offset on the wrong side of a window.
OFFSETS_30D = [3, 12, 27]            # inside 30d
OFFSETS_90D = [40, 60, 85]           # inside 90d, outside 30d
OFFSETS_1Y = [120, 200, 330]         # inside 1y, outside 90d
OFFSETS_OVER_1Y = [400]              # only in `all` (> 1 year old)
ALL_OFFSETS = OFFSETS_30D + OFFSETS_90D + OFFSETS_1Y + OFFSETS_OVER_1Y


def _seed_window_fixtures(install_dir: Path) -> None:
    """One session + one token_usage row per offset, dated N days ago."""
    with _open_db(install_dir) as conn:
        for n in ALL_OFFSETS:
            conn.execute(
                """
                INSERT INTO sessions(
                    session_id, source, model, started_at, ended_at,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_create_tokens, total_tokens, effective_tokens,
                    cost_usd, cost_source)
                VALUES(?, 'ide', 'claude-sonnet-4-6',
                       datetime('now', ?), datetime('now', ?, '+5 minutes'),
                       1000, 500, 0, 0, 1500, 1500, 0.01, 'api_pool')
                """,
                (f"win-{n:04d}", f"-{n} days", f"-{n} days"),
            )
            conn.execute(
                """
                INSERT INTO token_usage(
                    date, model, source,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_create_tokens)
                VALUES(DATE('now', ?), 'claude-sonnet-4-6', 'ide',
                       1000, 500, 0, 0)
                """,
                (f"-{n} days",),
            )


def _seed_bulk(install_dir: Path, n_sessions: int = 300, n_days: int = 360) -> None:
    """Volume for the pagination (stop 6) + performance (stop 9) checks.

    Sessions spread over the last `n_days` (all inside 1y, so they never
    collide with the >1-year fixture). token_usage gets one row per day
    (OR IGNORE skips dates the window fixture already seeded)."""
    with _open_db(install_dir) as conn:
        for d in range(1, n_days + 1):
            conn.execute(
                """
                INSERT OR IGNORE INTO token_usage(
                    date, model, source,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_create_tokens)
                VALUES(DATE('now', ?), 'claude-sonnet-4-6', 'ide',
                       1000, 500, 0, 0)
                """,
                (f"-{d} days",),
            )
        for i in range(n_sessions):
            d = (i % n_days) + 1
            conn.execute(
                """
                INSERT INTO sessions(
                    session_id, source, model, started_at, ended_at,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_create_tokens, total_tokens, effective_tokens,
                    cost_usd, cost_source)
                VALUES(?, 'ide', 'claude-sonnet-4-6',
                       datetime('now', ?), datetime('now', ?, '+5 minutes'),
                       1000, 500, 0, 0, 1500, 1500, 0.01, 'api_pool')
                """,
                (f"bulk-{i:04d}", f"-{d} days", f"-{d} days"),
            )


def _usage_dates(range_: str) -> set[str]:
    body = _api_get(f"/api/usage/tokens?range={range_}")
    return {r["date"] for r in body["daily"]}


def _outcome_dates(range_: str) -> set[str]:
    body = _api_get(f"/api/sessions/outcomes?range={range_}")
    return {r["date"] for r in body["daily"]}


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------

def run(install_dir: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))

    # ----- Stop 1: timerange predicates -----
    print("\nstop 1 · timerange predicates + normalize")
    from helpers import timerange as tr
    _check("1a · ALLOWED gains 90d + 1y",
           "90d" in tr.ALLOWED and "1y" in tr.ALLOWED,
           f"ALLOWED={tr.ALLOWED}")
    frag90, p90 = tr.sql_predicate("90d", "started_at")
    _check("1b · sql_predicate('90d') → -90 days fragment",
           frag90 == "started_at >= datetime('now', '-90 days')" and p90 == [],
           f"frag={frag90!r}")
    frag1y, p1y = tr.sql_predicate("1y", "started_at")
    _check("1c · sql_predicate('1y') → -1 year fragment",
           frag1y == "started_at >= datetime('now', '-1 year')" and p1y == [],
           f"frag={frag1y!r}")
    fragall, _ = tr.sql_predicate("all")
    _check("1d · sql_predicate('all') → 1=1 (unchanged)",
           fragall == "1=1", f"frag={fragall!r}")
    _check("1e · normalize passes 90d/1y/all through, coerces bogus→7d",
           tr.normalize("90d") == "90d" and tr.normalize("1y") == "1y"
           and tr.normalize("all") == "all" and tr.normalize("bogus") == "7d",
           f"got={[tr.normalize(x) for x in ('90d','1y','all','bogus')]}")

    # ----- Stop 2: days_in_range no longer KeyErrors -----
    print("\nstop 2 · days_in_range covers every ALLOWED value")
    try:
        mapping = {r: tr.days_in_range(r) for r in tr.ALLOWED}
        no_keyerror = True
    except KeyError as exc:
        mapping, no_keyerror = {}, False
        print(f"    KeyError: {exc}")
    _check("2a · days_in_range never KeyErrors on an ALLOWED value",
           no_keyerror, f"mapping={mapping}")
    _check("2b · 90d→90, 1y→365, all→365",
           mapping.get("90d") == 90 and mapping.get("1y") == 365
           and mapping.get("all") == 365,
           f"mapping={mapping}")

    # ----- Stop 5: empty / fresh DB returns clean zeros (run before seeding) -----
    print("\nstop 5 · empty DB · ?range=all returns clean empties (no crash)")
    ut = _api_get("/api/usage/tokens?range=all")
    _check("5a · /api/usage/tokens?range=all → daily:[] on empty DB",
           ut.get("daily") == [], f"daily={ut.get('daily')}")
    _check("5b · totals are all-zero, not null",
           ut.get("totals") == {"input": 0, "output": 0, "cache_read": 0,
                                "cache_create": 0, "total": 0},
           f"totals={ut.get('totals')}")
    so = _api_get("/api/sessions/outcomes?range=all")
    _check("5c · /api/sessions/outcomes?range=all → daily:[] on empty DB",
           so.get("daily") == [] and so.get("totals", {}).get("total") == 0,
           f"daily={so.get('daily')} totals={so.get('totals')}")
    sess = _api_get("/api/sessions?range=all")
    _check("5d · /api/sessions?range=all → items:[], total:0 on empty DB",
           sess.get("items") == [] and sess.get("total") == 0,
           f"total={sess.get('total')}")

    # Seed the multi-month window fixtures (10 distinct days; one > 1yr).
    _seed_window_fixtures(install_dir)

    # ----- Stop 3: wider window returns older rows the narrower one omits -----
    print("\nstop 3 · wider windows ⊇ narrower; counts monotonic non-decreasing")
    u30, u90, u1y, uall = (_usage_dates(r) for r in ("30d", "90d", "1y", "all"))
    _check("3a · usage/tokens date-set chain 30d ⊆ 90d ⊆ 1y ⊆ all",
           u30 <= u90 <= u1y <= uall,
           f"|30d|={len(u30)} |90d|={len(u90)} |1y|={len(u1y)} |all|={len(uall)}")
    _check("3b · usage/tokens counts strictly grow per widening (3/6/9/10)",
           (len(u30), len(u90), len(u1y), len(uall)) == (3, 6, 9, 10),
           f"counts={(len(u30), len(u90), len(u1y), len(uall))}")
    _check("3c · 90d surfaces day(s) the 30d query omits",
           len(u90 - u30) >= 1, f"new in 90d={sorted(u90 - u30)}")
    o30, o90, o1y, oall = (_outcome_dates(r) for r in ("30d", "90d", "1y", "all"))
    _check("3d · sessions/outcomes date-set chain 30d ⊆ 90d ⊆ 1y ⊆ all",
           o30 <= o90 <= o1y <= oall,
           f"|30d|={len(o30)} |90d|={len(o90)} |1y|={len(o1y)} |all|={len(oall)}")
    _check("3e · sessions/outcomes counts strictly grow per widening (3/6/9/10)",
           (len(o30), len(o90), len(o1y), len(oall)) == (3, 6, 9, 10),
           f"counts={(len(o30), len(o90), len(o1y), len(oall))}")

    # ----- Stop 4: `all` includes the >1-year-old row; 1y / 30d exclude it -----
    print("\nstop 4 · >1yr-old row visible only under `all`")
    with _open_db(install_dir) as conn:
        over_usage = conn.execute(
            "SELECT DATE('now', '-400 days') AS d").fetchone()["d"]
        over_outcome = conn.execute(
            "SELECT DATE('now', '-400 days', 'localtime') AS d").fetchone()["d"]
    _check("4a · usage/tokens: >1yr date in `all`, absent from `1y`",
           over_usage in uall and over_usage not in u1y,
           f"date={over_usage} in_all={over_usage in uall} in_1y={over_usage in u1y}")
    _check("4b · usage/tokens: >1yr date also absent from `30d`",
           over_usage not in u30, f"date={over_usage}")
    _check("4c · sessions/outcomes: >1yr date in `all`, absent from `1y`",
           over_outcome in oall and over_outcome not in o1y,
           f"date={over_outcome}")

    # Bulk volume for pagination + performance.
    _seed_bulk(install_dir)

    # ----- Stop 6: sessions list paginates under `all` (respects limit) -----
    print("\nstop 6 · /api/sessions?range=all paginates (limit honoured, offset walks)")
    p1 = _api_get("/api/sessions?range=all&limit=10&offset=0")
    p2 = _api_get("/api/sessions?range=all&limit=10&offset=10")
    _check("6a · limit honoured — page is 10 rows, not the whole table",
           len(p1["items"]) == 10, f"len={len(p1['items'])}")
    _check("6b · total reflects full history and exceeds the page size",
           p1["total"] >= 300 and p1["total"] > len(p1["items"]),
           f"total={p1['total']}")
    ids1 = {it["session_id"] for it in p1["items"]}
    ids2 = {it["session_id"] for it in p2["items"]}
    _check("6c · offset walks to a disjoint older page",
           len(ids1) == 10 and len(ids2) == 10 and ids1.isdisjoint(ids2),
           f"overlap={ids1 & ids2}")
    starts = [it["started_at"] for it in p1["items"]]
    _check("6d · page ordered newest-first (started_at DESC)",
           starts == sorted(starts, reverse=True),
           f"first={starts[0] if starts else None} last={starts[-1] if starts else None}")

    # ----- Stop 9: performance sanity under `all` -----
    print("\nstop 9 · `all` completes well under 1s with a few hundred rows")
    _, ms_tok = _api_get_timed("/api/usage/tokens?range=all")
    _check("9a · /api/usage/tokens?range=all < 1000ms",
           ms_tok < 1000.0, f"{ms_tok:.1f}ms")
    _, ms_out = _api_get_timed("/api/sessions/outcomes?range=all")
    _check("9b · /api/sessions/outcomes?range=all < 1000ms",
           ms_out < 1000.0, f"{ms_out:.1f}ms")
    _, ms_sess = _api_get_timed("/api/sessions?range=all&limit=100")
    _check("9c · /api/sessions?range=all < 1000ms",
           ms_sess < 1000.0, f"{ms_sess:.1f}ms")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cc-v068-smoke-")
    install_dir = Path(tmp)
    (install_dir / "data").mkdir(parents=True, exist_ok=True)
    print(f"smoke install dir: {install_dir}")

    server_proc = _start_server(install_dir)
    try:
        os.environ["CC_INSTALL_DIR"] = str(install_dir)
        run(install_dir)
    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()

    print("\n=== summary ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"  {passed} / {total} checks passed")
    failed = [name for name, ok, _ in RESULTS if not ok]
    if failed:
        print("\n  FAILED:")
        for name in failed:
            print(f"    - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
