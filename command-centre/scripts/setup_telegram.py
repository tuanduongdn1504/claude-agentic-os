"""Interactive Telegram bridge wizard.

Prompts for a bot token, verifies via getMe, then helps capture the
chat_id by listening for the next message the user sends to the bot.
Writes both into $INSTALL_DIR/.env without touching anything else.

Usage:
    setup_telegram.py                 # interactive
    setup_telegram.py --token <T>     # token via flag (still prompts for chat)
    setup_telegram.py --token <T> --chat <ID>   # fully unattended
    setup_telegram.py --dry-run       # show what would be written
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


# -- styling ------------------------------------------------------------
_IS_TTY = sys.stdout.isatty()
def _c(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _IS_TTY else s
def ok(s: str) -> str: return _c(s, "32;1")
def err(s: str) -> str: return _c(s, "31;1")
def dim(s: str) -> str: return _c(s, "2")


_API = "https://api.telegram.org"


def _tg(method: str, token: str, payload: Optional[dict] = None,
        timeout: float = 10.0) -> dict:
    url = f"{_API}/bot{token}/{method}"
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(f"telegram {method} failed: {body}")
    return body.get("result") or {}


def _verify_token(token: str) -> dict:
    try:
        me = _tg("getMe", token)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"telegram returned HTTP {e.code} — token likely invalid") from e
    return me


def _capture_chat_id(token: str, prior_offset: int) -> Optional[str]:
    """Long-poll for one message, return its chat.id."""
    print(dim("  waiting for a message…  (send any text to the bot from your phone)"))
    deadline = time.monotonic() + 180
    offset = prior_offset
    while time.monotonic() < deadline:
        url = f"{_API}/bot{token}/getUpdates?timeout=20&offset={offset}"
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                body = json.loads(r.read())
        except Exception as exc:
            print(err(f"  getUpdates failed: {exc!r} — retrying"))
            time.sleep(2)
            continue
        if not body.get("ok"):
            print(err(f"  getUpdates returned not-ok: {body}"))
            return None
        for update in body.get("result", []):
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            msg = update.get("message") or update.get("channel_post")
            if msg:
                chat_id = ((msg.get("chat") or {}).get("id"))
                if chat_id is not None:
                    return str(chat_id)
        # Loop continues — getUpdates with timeout=20 already blocked.
    return None


def _baseline_offset(token: str) -> int:
    """Fetch current offset so old test messages don't shadow the live capture."""
    url = f"{_API}/bot{token}/getUpdates?timeout=0"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            body = json.loads(r.read())
        results = body.get("result", [])
        if not results:
            return 0
        return max(int(u.get("update_id", 0)) for u in results) + 1
    except Exception:
        return 0


# -- .env merge ---------------------------------------------------------

def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def _write_env(path: Path, current: dict[str, str], updates: dict[str, str]) -> None:
    """Rewrite preserving order + comments. Overwrites in-place if key exists,
    appends at the bottom otherwise. Creates a timestamped backup if the file
    already existed."""
    existing_keys: set[str] = set(current.keys())
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_suffix(path.suffix + f".bak.{stamp}"))

    lines: list[str] = []
    seen: set[str] = set()
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    line = f"{k}={updates[k]}"
                    seen.add(k)
            lines.append(line)
    # Append any keys we didn't see.
    for k, v in updates.items():
        if k in seen:
            continue
        lines.append(f"{k}={v}")
        seen.add(k)
    path.write_text("\n".join(lines).rstrip() + "\n")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


# -- main ---------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Telegram bridge wizard")
    ap.add_argument("--token", help="bot token (skips the prompt)")
    ap.add_argument("--chat", help="chat id (skips the capture step)")
    ap.add_argument("--env", default=None,
                    help="path to .env (default: $CC_INSTALL_DIR/.env)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    install_dir = Path(os.environ.get("CC_INSTALL_DIR") or
                       Path(__file__).resolve().parents[1])
    env_path = Path(args.env) if args.env else install_dir / ".env"

    print(f"Telegram wizard · env: {env_path}\n")

    token = args.token
    if not token:
        try:
            token = input("Bot token (from @BotFather): ").strip()
        except EOFError:
            token = ""
    if not token:
        print(err("no token — aborted"))
        return 1

    print(dim("  verifying token via getMe…"))
    try:
        me = _verify_token(token)
    except Exception as exc:
        print(err(f"  failed: {exc}"))
        return 2
    print(ok(f"  bot @{me.get('username')} (id={me.get('id')}) — looks good"))

    chat_id = args.chat
    if not chat_id:
        baseline = _baseline_offset(token)
        chat_id = _capture_chat_id(token, baseline)
        if not chat_id:
            print(err("  no message received in 3 minutes — aborted"))
            print(dim("  rerun once you've sent a message to the bot, "
                      "or pass --chat <id> directly."))
            return 3
        print(ok(f"  chat id captured: {chat_id}"))

    if args.dry_run:
        print(dim(f"\n--dry-run: would write TELEGRAM_BOT_TOKEN + "
                  f"TELEGRAM_DASH_CHAT_ID={chat_id} to {env_path}"))
        return 0

    current = _read_env(env_path)
    updates = {
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_DASH_CHAT_ID": chat_id,
    }
    _write_env(env_path, current, updates)
    print(ok(f"\n  wrote {env_path}"))
    print(dim("  load the launchd agent (or restart it) to start the bridge:"))
    print(dim("    launchctl unload ~/Library/LaunchAgents/com.commandcentre.telegram-bridge.plist 2>/dev/null"))
    print(dim("    launchctl load   ~/Library/LaunchAgents/com.commandcentre.telegram-bridge.plist"))
    print(dim("  or run the bridge in foreground:  cc setup telegram --foreground"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
