"""/api/skills, /api/skills/sync, /api/skills/{name}/autonomy, plus
v0.6.0 launcher endpoints: PATCH .../preset and POST .../launch."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

import db
import sync_skills
from helpers import timerange

router = APIRouter()

_ALLOWED_AUTONOMY = ("auto", "review", "manual")
_ALLOWED_MODES = ("classic", "stream")
_ALLOWED_QUADRANTS = ("do", "schedule", "delegate", "archive")
_ALLOWED_RISKS = ("low", "medium", "high")

# v0.6.0 — preset schema. Every field optional; absent = use fallback chain.
_PRESET_FIELDS = (
    "title", "description", "model", "execution_mode",
    "priority", "quadrant", "risk_level",
    "requires_approval", "dry_run",
)


def _validate_preset(preset: Any) -> dict[str, Any]:
    """Validate the preset blob. Raise HTTPException(400) on bad fields.
    Returns the cleaned dict (drops unknown keys, coerces flags to bool)."""
    if preset is None:
        return {}
    if not isinstance(preset, dict):
        raise HTTPException(400, "preset must be an object or null")
    cleaned: dict[str, Any] = {}
    for k in _PRESET_FIELDS:
        if k not in preset:
            continue
        v = preset[k]
        if v is None:
            continue
        if k == "execution_mode" and v not in _ALLOWED_MODES:
            raise HTTPException(400, f"execution_mode must be one of {_ALLOWED_MODES}")
        if k == "quadrant" and v not in _ALLOWED_QUADRANTS:
            raise HTTPException(400, f"quadrant must be one of {_ALLOWED_QUADRANTS}")
        if k == "risk_level" and v not in _ALLOWED_RISKS:
            raise HTTPException(400, f"risk_level must be one of {_ALLOWED_RISKS}")
        if k == "priority":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise HTTPException(400, "priority must be an integer")
        if k in ("requires_approval", "dry_run"):
            v = bool(v)
        if k in ("title", "description", "model") and not isinstance(v, str):
            raise HTTPException(400, f"{k} must be a string")
        cleaned[k] = v
    return cleaned


def _parse_preset_json(blob: str | None) -> dict[str, Any] | None:
    if not blob:
        return None
    try:
        parsed = json.loads(blob)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _avg_cost_30d(conn, name: str) -> float | None:
    row = conn.execute(
        """
        SELECT AVG(cost_usd) AS avg
        FROM ops_tasks
        WHERE assigned_skill = ?
          AND cost_usd IS NOT NULL
          AND created_at >= datetime('now', '-30 days')
        """,
        (name,),
    ).fetchone()
    if not row or row["avg"] is None:
        return None
    return round(float(row["avg"]), 6)


def _today_cost_usd(conn, name: str) -> float:
    """Sum of api_pool cost for this skill, completed today (local). Mirrors
    the dispatcher's global-cap predicate so /api/skills + dispatcher math
    stay aligned. Always returns a number — 0.0 when nothing has completed."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_usd), 0) AS s
        FROM ops_tasks
        WHERE assigned_skill = ?
          AND cost_source = 'api_pool'
          AND cost_usd IS NOT NULL
          AND DATE(completed_at, 'localtime') = DATE('now', 'localtime')
        """,
        (name,),
    ).fetchone()
    return round(float(row["s"]) if row and row["s"] is not None else 0.0, 6)


@router.get("/api/skills")
async def list_skills(environment: Optional[str] = None,
                      user_invocable: Optional[int] = None) -> dict[str, Any]:
    clauses = ["1=1"]
    params: list[Any] = []
    if environment:
        clauses.append("environment = ?"); params.append(environment)
    if user_invocable is not None:
        clauses.append("user_invocable = ?"); params.append(1 if user_invocable else 0)
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT name, environment, description, path, autonomy_level,
                   user_invocable, script_count, last_modified,
                   preset_json, last_launched_at,
                   COALESCE(launch_count, 0) AS launch_count,
                   daily_budget_usd
            FROM skills
            WHERE {' AND '.join(clauses)}
            ORDER BY environment, name
            """,
            params,
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["preset"] = _parse_preset_json(d.pop("preset_json", None))
            d["avg_cost_usd_30d"] = _avg_cost_30d(conn, d["name"])
            d["today_cost_usd"] = _today_cost_usd(conn, d["name"])
            items.append(d)
    return {"items": items, "count": len(items)}


@router.post("/api/skills/sync")
async def skills_sync() -> dict[str, Any]:
    result = await asyncio.to_thread(sync_skills.run_sync, False)
    return {"ok": True, **result}


@router.get("/api/skills/economics")
async def skills_economics(range: str = "30d") -> dict[str, Any]:
    """Token / cost rollup per skill. Sources OTEL events that carry
    skill_name. Falls back to 0-cost when OTEL hasn't seen a skill."""
    pred, params = timerange.sql_predicate(range, "timestamp")
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT skill_name,
                   COUNT(*) AS invocations,
                   COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) AS effective_tokens,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM otel_events
            WHERE skill_name IS NOT NULL AND {pred}
            GROUP BY skill_name
            ORDER BY cost_usd DESC, effective_tokens DESC
            LIMIT 100
            """,
            params,
        ).fetchall()
    return {"range": timerange.normalize(range),
            "items": [dict(r) for r in rows], "count": len(rows)}


@router.patch("/api/skills/{name}/autonomy")
async def skills_autonomy(name: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    level = (payload.get("autonomy_level") or "").lower()
    if level not in _ALLOWED_AUTONOMY:
        raise HTTPException(400, f"autonomy_level must be one of {_ALLOWED_AUTONOMY}")
    with db.connect() as conn:
        rc = conn.execute(
            "UPDATE skills SET autonomy_level = ? WHERE name = ?", (level, name),
        ).rowcount
    if rc == 0:
        raise HTTPException(404, "skill not found")
    return {"updated": True, "name": name, "autonomy_level": level}


@router.patch("/api/skills/{name}/budget")
async def skills_budget(name: str, request: Request) -> dict[str, Any]:
    """v0.6.7 — set or clear the per-skill daily cost budget.

    Body: `{"daily_budget_usd": float | null}`. `null` clears (unlimited);
    `0` is valid and blocks all claims until the operator lifts it; negative
    numbers and non-numeric values are 400. Mirrors PATCH .../autonomy as
    the column-update endpoint pattern (no JSON blob)."""
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "request body required (use null to clear)")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(payload, dict) or "daily_budget_usd" not in payload:
        raise HTTPException(400, "body must include 'daily_budget_usd'")

    value = payload["daily_budget_usd"]
    if value is None:
        stored: float | None = None
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(400, "daily_budget_usd must be a number or null")
        if value < 0:
            raise HTTPException(400, "daily_budget_usd must be >= 0 (or null to clear)")
        stored = float(value)

    with db.connect() as conn:
        rc = conn.execute(
            "UPDATE skills SET daily_budget_usd = ? WHERE name = ?",
            (stored, name),
        ).rowcount
        if rc == 0:
            raise HTTPException(404, "skill not found")
        return _skill_row(conn, name)


# ---------------------------------------------------------------------------
# v0.6.0 — preset editor + launcher
# ---------------------------------------------------------------------------

def _skill_row(conn, name: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT name, environment, description, path, autonomy_level,
               user_invocable, script_count, last_modified,
               preset_json, last_launched_at,
               COALESCE(launch_count, 0) AS launch_count,
               daily_budget_usd
        FROM skills WHERE name = ?
        """,
        (name,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "skill not found")
    d = dict(row)
    d["preset"] = _parse_preset_json(d.pop("preset_json", None))
    d["avg_cost_usd_30d"] = _avg_cost_30d(conn, name)
    d["today_cost_usd"] = _today_cost_usd(conn, name)
    return d


@router.patch("/api/skills/{name}/preset")
async def skills_preset(name: str, request: Request) -> dict[str, Any]:
    """Replace (not merge) the launch preset for this skill. Body is the
    full preset object, or `null` to clear."""
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "request body required (use null to clear)")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(400, "body must be JSON")

    if payload is None:
        blob = None
    else:
        cleaned = _validate_preset(payload)
        blob = json.dumps(cleaned, ensure_ascii=False)

    with db.connect() as conn:
        rc = conn.execute(
            "UPDATE skills SET preset_json = ? WHERE name = ?",
            (blob, name),
        ).rowcount
        if rc == 0:
            raise HTTPException(404, "skill not found")
        return _skill_row(conn, name)


def _trigger_dispatcher_inline() -> None:
    """Fire `heartbeat.py --once` detached. Mirrors /api/dispatcher/trigger
    but runs synchronously inside the launch handler's worker thread so
    the dispatched task starts within ~1s instead of waiting for the next
    120s heartbeat. Best-effort — silently no-ops if the script is
    missing (e.g. dev mode without Mission Control installed)."""
    heartbeat = (
        Path(db.INSTALL_DIR) / ".claude" / "skills"
        / "mission-control" / "scripts" / "heartbeat.py"
    )
    if not heartbeat.exists():
        return
    py = sys.executable or "/usr/bin/python3"
    env = os.environ.copy()
    env.setdefault("CC_INSTALL_DIR", str(db.INSTALL_DIR))
    try:
        subprocess.Popen(
            [py, str(heartbeat), "--once"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            cwd=str(db.INSTALL_DIR),
        )
    except Exception as exc:
        print(f"[skills/launch] dispatcher trigger failed: {exc!r}", file=sys.stderr)


def _resolve_launch_fields(
    name: str, preset: dict[str, Any] | None,
    description_override: str | None,
    skill_description: str | None,
) -> dict[str, Any]:
    """Apply the preset → frontmatter → hardcoded-default fallback chain
    per amendment §API surface delta · Preset fallback chain."""
    p = preset or {}
    default_model = os.environ.get("MISSION_CONTROL_DEFAULT_MODEL") or None
    description = description_override
    if description is None:
        description = p.get("description") or skill_description or None
    return {
        "title":             p.get("title")             or f"Run {name}",
        "description":       description,
        "model":             p.get("model")             or default_model,
        "execution_mode":    p.get("execution_mode")    or "classic",
        "priority":          p.get("priority")          if p.get("priority") is not None else 0,
        "quadrant":          p.get("quadrant")          or "do",
        "risk_level":        p.get("risk_level")        or "low",
        "requires_approval": bool(p.get("requires_approval", False)),
        "dry_run":           bool(p.get("dry_run", False)),
    }


@router.post("/api/skills/{name}/launch")
async def skills_launch(name: str, request: Request) -> dict[str, Any]:
    """Queue a one-click run of this skill using its stored preset.

    Body: `{description_override?: string}` — optional one-shot override
    for the task description. Everything else resolves through the
    fallback chain in `_resolve_launch_fields`.

    Returns `{task_id, status}` immediately. Dispatcher is poked inline
    so the run starts within ~1s instead of waiting for the next 120s
    heartbeat.
    """
    payload: dict[str, Any] = {}
    raw = await request.body()
    if raw:
        try:
            payload = json.loads(raw) or {}
        except Exception:
            raise HTTPException(400, "body must be JSON")
    description_override = payload.get("description_override")
    if description_override is not None and not isinstance(description_override, str):
        raise HTTPException(400, "description_override must be a string")

    with db.connect() as conn:
        skill = _skill_row(conn, name)
        fields = _resolve_launch_fields(
            name, skill.get("preset"),
            description_override, skill.get("description"),
        )
        status = "awaiting_approval" if fields["requires_approval"] else "pending"
        cur = conn.execute(
            """
            INSERT INTO ops_tasks(
                title, description, status, priority, assigned_skill, model,
                execution_mode, requires_approval, risk_level, dry_run,
                quadrant, cost_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["title"], fields["description"], status, fields["priority"],
                name, fields["model"], fields["execution_mode"],
                int(fields["requires_approval"]), fields["risk_level"],
                int(fields["dry_run"]), fields["quadrant"],
                "api_pool",
            ),
        )
        task_id = int(cur.lastrowid or 0)
        conn.execute(
            """
            UPDATE skills SET
                launch_count    = COALESCE(launch_count, 0) + 1,
                last_launched_at = datetime('now')
            WHERE name = ?
            """,
            (name,),
        )

    # Inline dispatcher trigger so the run starts immediately.
    await asyncio.to_thread(_trigger_dispatcher_inline)
    return {"task_id": task_id, "status": status, "skill": name}
