"""/api/skills, /api/skills/sync, /api/skills/{name}/autonomy."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

import db
import sync_skills
from helpers import timerange

router = APIRouter()

_ALLOWED_AUTONOMY = ("auto", "review", "manual")


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
                   user_invocable, script_count, last_modified
            FROM skills
            WHERE {' AND '.join(clauses)}
            ORDER BY environment, name
            """,
            params,
        ).fetchall()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


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
