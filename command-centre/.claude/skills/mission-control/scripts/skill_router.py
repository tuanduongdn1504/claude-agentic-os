"""Pick a skill for an unassigned task.

The prompt calls for a Haiku-backed picker. We ship a deterministic
fallback here — keyword-match + shortest-description tiebreak — and stub
the Haiku path so install.sh can wire it up later without touching the
dispatcher call sites.

Cheap: ~0 USD. Good enough for a local dashboard; the real win is
letting the UI ship without an API-key dependency.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS = Path(os.environ.get("CC_INSTALL_DIR") or
                (Path(__file__).resolve().parents[4])) / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import db  # noqa: E402


def _load_skills() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT name, description, autonomy_level FROM skills "
            "WHERE user_invocable = 1"
        ).fetchall()
    return [dict(r) for r in rows]


def pick(title: str, description: str | None = None) -> str | None:
    """Return a skill name, or None if we can't decide. Never raises."""
    haystack = f"{title} {description or ''}".lower()
    skills = _load_skills()
    if not skills:
        return None
    # Score = count of description keywords that appear in the task text.
    # Require ≥ 2 matches to prevent a single coincidental token hijacking
    # the autonomy gate (otherwise every task gets routed to some skill).
    best = None
    best_score = 1  # strictly greater-than → need ≥ 2
    for s in skills:
        desc = (s.get("description") or "").lower()
        tokens = {w for w in desc.replace(",", " ").split() if len(w) >= 4}
        score = sum(1 for tok in tokens if tok in haystack)
        if score > best_score:
            best_score = score
            best = s["name"]
    return best
