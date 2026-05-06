"""Rebuild the `skills` registry table.

Sources (in this order):
  1. `~/.claude/skills/*/SKILL.md` or `*.md` — ide:global scope
  2. `$CC_PROJECT_ROOT/.claude/skills/*/SKILL.md` — ide:project scope
  3. `$CC_INSTALL_DIR/.claude/skills/*/` — install-local skills
  (cowork:plugin / cowork:scheduled left for future — depend on Cowork
  layout we don't yet have here.)

Front-matter is optional. We read at most the first ~6 KB per skill file
and pull `description` from the YAML preamble if present. Script count =
.py files in the skill's scripts/ subdir (or the same dir).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402


FRONTMATTER_RE_BYTES = 8192


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML front-matter parser: only `key: value` lines between
    two `---` markers. No nesting, no lists. Sufficient for skill files."""
    if not text.startswith("---"):
        return {}
    out: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for line in lines[1:]:
        s = line.strip()
        if s == "---":
            break
        if ":" in s:
            k, _, v = s.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _scan_dir(root: Path, environment: str) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    # Look for SKILL.md first, fall back to <dir>/<dir>.md or top-level .md.
    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        skill_md = None
        for candidate in ("SKILL.md", f"{skill_dir.name}.md", "skill.md"):
            cand = skill_dir / candidate
            if cand.exists():
                skill_md = cand; break
        if not skill_md:
            # Also tolerate a flat layout: root/foo.md with no dir.
            continue
        desc = ""
        try:
            text = skill_md.read_text(errors="replace")[:FRONTMATTER_RE_BYTES]
            fm = _parse_frontmatter(text)
            desc = fm.get("description") or ""
        except Exception:
            pass
        scripts_dir = skill_dir / "scripts" if (skill_dir / "scripts").exists() else skill_dir
        script_count = 0
        try:
            script_count = sum(1 for p in scripts_dir.rglob("*.py"))
        except Exception:
            pass
        out.append({
            "name": skill_dir.name,
            "environment": environment,
            "description": desc,
            "path": str(skill_md),
            "autonomy_level": "review",
            "user_invocable": 1,
            "script_count": script_count,
            "last_modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(skill_md.stat().st_mtime)),
        })
    # Also allow flat-file skills at the top level.
    for md in sorted(root.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue
        try:
            text = md.read_text(errors="replace")[:FRONTMATTER_RE_BYTES]
            fm = _parse_frontmatter(text)
        except Exception:
            fm = {}
        out.append({
            "name": md.stem,
            "environment": environment,
            "description": fm.get("description") or "",
            "path": str(md),
            "autonomy_level": "review",
            "user_invocable": 1,
            "script_count": 0,
            "last_modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(md.stat().st_mtime)),
        })
    return out


def run_sync(verbose: bool = False) -> dict[str, int]:
    discovered: list[dict[str, Any]] = []
    discovered += _scan_dir(Path(os.path.expanduser("~/.claude/skills")), "ide:global")
    project_root = os.environ.get("CC_PROJECT_ROOT")
    if project_root:
        discovered += _scan_dir(Path(project_root) / ".claude" / "skills", "ide:project")
    discovered += _scan_dir(db.INSTALL_DIR / ".claude" / "skills", "ide:project")

    # Dedupe by (name, environment) — last write wins.
    with db.connect() as conn:
        # Don't wipe user-set autonomy. Upsert by PK.
        for sk in discovered:
            conn.execute(
                """
                INSERT INTO skills(name, environment, description, path, autonomy_level,
                                   user_invocable, script_count, last_modified)
                VALUES (:name, :environment, :description, :path, :autonomy_level,
                        :user_invocable, :script_count, :last_modified)
                ON CONFLICT(name) DO UPDATE SET
                    environment=excluded.environment,
                    description=excluded.description,
                    path=excluded.path,
                    user_invocable=excluded.user_invocable,
                    script_count=excluded.script_count,
                    last_modified=excluded.last_modified
                """,
                sk,
            )
        conn.execute(
            "INSERT INTO activities(event_type, detail) VALUES ('skills_sync', ?)",
            (f"discovered={len(discovered)}",),
        )
    if verbose:
        print(f"skills_sync: {len(discovered)} discovered")
    return {"discovered": len(discovered)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run_sync(verbose=True), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
