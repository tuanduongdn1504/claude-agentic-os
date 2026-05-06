#!/bin/bash
# Command Centre installer.
#
# Copies this repo into $INSTALL_DIR (default ~/.command-centre), creates
# a venv, builds the UI if not already built, writes launcher scripts,
# optionally runs the OTEL wizard, optionally loads launchd plists,
# optionally starts the server.
#
# Flags (all optional; sensible defaults for unattended installs):
#   --install-dir=PATH    default ~/.command-centre
#   --project-root=PATH   default $PWD if it has .claude/ else prompt
#   --port=N              default 8765
#   --model=M             default claude-sonnet-4-6
#   --no-otel             skip OTEL wizard
#   --no-launchd          skip launchctl load
#   --no-telegram         skip Telegram prompt (default — bridge not yet built)
#   --telegram            run Telegram wizard if present
#   --no-start            don't start server at the end
#   --no-build-ui         skip `npm install && npm run build` (expects ui/dist already)
#   --yes                 non-interactive (assume Y for all prompts)
#   --force               wipe $INSTALL_DIR/scripts + .claude/skills before copy
#
# Idempotent: safe to re-run. On re-run the venv is reused, scripts are
# re-synced (`rsync --delete` scoped to the two code dirs — user data under
# $INSTALL_DIR/data is never touched).
set -euo pipefail

# ---------- defaults ----------
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.command-centre"
PROJECT_ROOT=""
PORT="8765"
MODEL="claude-sonnet-4-6"
DO_OTEL=1
DO_LAUNCHD=1
DO_TELEGRAM=0
DO_START=1
DO_BUILD_UI=1
FORCE=0
YES=0

# ---------- ansi ----------
if [ -t 1 ]; then RED=$'\033[31;1m'; GREEN=$'\033[32;1m'; YEL=$'\033[33;1m'; DIM=$'\033[2m'; R=$'\033[0m'; else RED=""; GREEN=""; YEL=""; DIM=""; R=""; fi
say() { printf "%s\n" "$*"; }
step() { printf "\n%s→ %s%s\n" "$GREEN" "$*" "$R"; }
warn() { printf "%s! %s%s\n" "$YEL" "$*" "$R"; }
die()  { printf "%s✗ %s%s\n" "$RED" "$*" "$R" >&2; exit 1; }

# ---------- args ----------
for arg in "$@"; do
  case "$arg" in
    --install-dir=*)   INSTALL_DIR="${arg#*=}" ;;
    --project-root=*)  PROJECT_ROOT="${arg#*=}" ;;
    --port=*)          PORT="${arg#*=}" ;;
    --model=*)         MODEL="${arg#*=}" ;;
    --no-otel)         DO_OTEL=0 ;;
    --no-launchd)      DO_LAUNCHD=0 ;;
    --no-telegram)     DO_TELEGRAM=0 ;;
    --telegram)        DO_TELEGRAM=1 ;;
    --no-start)        DO_START=0 ;;
    --no-build-ui)     DO_BUILD_UI=0 ;;
    --yes|-y)          YES=1 ;;
    --force)           FORCE=1 ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown flag: $arg" ;;
  esac
done

ask() {
  # ask "question" "default" → echoes the chosen value
  local q="$1" d="${2:-}" a
  if [ "$YES" = "1" ]; then echo "$d"; return; fi
  if [ -n "$d" ]; then read -r -p "$q [$d] " a; else read -r -p "$q " a; fi
  echo "${a:-$d}"
}

# ---------- python detection ----------
step "Detecting Python"
PY=""
for cand in \
  /opt/homebrew/opt/python@3.12/libexec/bin/python3 \
  /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
  /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
  python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$(command -v "$cand")"; break; fi
done
[ -n "$PY" ] || die "no python3 found on PATH"
PYV=$("$PY" -c 'import sys;print("{}.{}.{}".format(*sys.version_info[:3]))')
say "  using $PY · $PYV"
MAJMIN=$("$PY" -c 'import sys;print("{}{:02d}".format(sys.version_info[0], sys.version_info[1]))')
if [ "$MAJMIN" -lt 309 ]; then die "need Python 3.9+, found $PYV"; fi
if [ "$MAJMIN" -lt 310 ]; then warn "Python 3.9 works but 3.10+ recommended"; fi

# ---------- project root ----------
step "Resolving project root"
if [ -z "$PROJECT_ROOT" ]; then
  if [ -d "$PWD/.claude" ]; then
    PROJECT_ROOT="$PWD"
  else
    PROJECT_ROOT="$(ask "path to a project with .claude/ (Enter for none)" "")"
  fi
fi
if [ -n "$PROJECT_ROOT" ]; then
  say "  project root: $PROJECT_ROOT"
else
  warn "no project root — dispatcher will cwd to install dir"
fi

# ---------- cowork auto-detect ----------
COWORK_DIR="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"
if [ -d "$COWORK_DIR" ]; then
  say "  cowork dir detected: $COWORK_DIR"
else
  COWORK_DIR=""
fi

# ---------- install layout ----------
step "Creating install layout at $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"/{scripts,data,logs,bin,ui,.claude,.tmp/mission-control-queue/pids,templates}
if [ "$FORCE" = "1" ]; then
  rm -rf "$INSTALL_DIR/scripts" "$INSTALL_DIR/.claude/skills"
  mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/.claude/skills"
fi

# Copy / sync code. rsync --delete only inside the two code roots so data is safe.
rsync -a --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$REPO_DIR/scripts/" "$INSTALL_DIR/scripts/"
rsync -a --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$REPO_DIR/.claude/" "$INSTALL_DIR/.claude/"
rsync -a \
  "$REPO_DIR/templates/" "$INSTALL_DIR/templates/"
cp -f "$REPO_DIR/requirements.txt" "$INSTALL_DIR/"
cp -f "$REPO_DIR/cc"               "$INSTALL_DIR/bin/cc"
chmod +x "$INSTALL_DIR/bin/cc"
[ -f "$REPO_DIR/.env.example" ] && cp -f "$REPO_DIR/.env.example" "$INSTALL_DIR/.env.example"

# ---------- UI build ----------
if [ -d "$REPO_DIR/ui" ]; then
  if [ "$DO_BUILD_UI" = "1" ] && [ ! -d "$REPO_DIR/ui/dist" ]; then
    step "Building UI"
    if command -v npm >/dev/null 2>&1; then
      (cd "$REPO_DIR/ui" && npm install --no-fund --no-audit --prefer-offline >/dev/null && npm run build >/dev/null) \
        || warn "UI build failed — dashboard will 404 until you rebuild"
    else
      warn "npm not found — skipping UI build. Install node then rerun with --no-build-ui=0"
    fi
  fi
  if [ -d "$REPO_DIR/ui/dist" ]; then
    rsync -a --delete "$REPO_DIR/ui/dist/" "$INSTALL_DIR/ui/dist/"
    say "  ui/dist synced → $INSTALL_DIR/ui/dist"
  else
    warn "ui/dist missing — dashboard HTML will 404; rerun after \`npm run build\` in ui/"
  fi
fi

# ---------- seed .env + config snapshot ----------
step "Seeding .env + config"
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp -f "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env" 2>/dev/null || touch "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi
cat > "$INSTALL_DIR/config" <<CONF
# Generated by install.sh — loaded by cc shim and start.sh.
# Safe to edit; re-running install.sh overwrites.
CC_INSTALL_DIR="$INSTALL_DIR"
CC_PORT="$PORT"
CC_HOST="127.0.0.1"
CC_DASHBOARD_URL="http://127.0.0.1:$PORT"
CC_PROJECT_ROOT="${PROJECT_ROOT}"
CC_COWORK_DIR="${COWORK_DIR}"
MISSION_CONTROL_DEFAULT_MODEL="$MODEL"
CONF
chmod 644 "$INSTALL_DIR/config"

# ---------- venv ----------
step "Creating venv"
VENV_DIR="$INSTALL_DIR/venv"
if [ ! -x "$VENV_DIR/bin/python3" ]; then
  "$PY" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python3" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python3" -m pip install --quiet -r "$INSTALL_DIR/requirements.txt"
say "  venv → $VENV_DIR"

# ---------- start.sh / stop.sh ----------
step "Writing launcher scripts"
cat > "$INSTALL_DIR/start.sh" <<'LAUNCH'
#!/bin/bash
set -e
cd "$(dirname "$0")"
# shellcheck disable=SC1091
set -a
. ./config
[ -f ./.env ] && . ./.env
set +a
exec "$CC_INSTALL_DIR/venv/bin/python3" scripts/server.py
LAUNCH
cat > "$INSTALL_DIR/stop.sh" <<'LAUNCH'
#!/bin/bash
set -e
cd "$(dirname "$0")"
pkill -f "$PWD/scripts/server.py" && echo "stopped" || echo "not running"
LAUNCH
chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/stop.sh"

# ---------- ~/.local/bin/cc symlink ----------
LOCAL_BIN="$HOME/.local/bin"
if [ -d "$LOCAL_BIN" ] && printf ':%s:' "$PATH" | grep -q ":$LOCAL_BIN:"; then
  ln -sf "$INSTALL_DIR/bin/cc" "$LOCAL_BIN/cc"
  say "  symlinked $LOCAL_BIN/cc → $INSTALL_DIR/bin/cc"
fi

# ---------- OTEL wizard ----------
if [ "$DO_OTEL" = "1" ]; then
  step "OTEL wizard"
  YES_FLAG=""; [ "$YES" = "1" ] && YES_FLAG="--yes"
  "$VENV_DIR/bin/python3" "$INSTALL_DIR/scripts/setup_otel.py" --port "$PORT" $YES_FLAG \
    || warn "OTEL wizard errored — rerun with \`cc setup otel\`"
else
  say "  skipping OTEL wizard (--no-otel)"
fi

# ---------- Telegram wizard ----------
if [ "$DO_TELEGRAM" = "1" ]; then
  if [ -f "$INSTALL_DIR/scripts/setup_telegram.py" ]; then
    step "Telegram wizard"
    "$VENV_DIR/bin/python3" "$INSTALL_DIR/scripts/setup_telegram.py" || warn "telegram wizard skipped"
  else
    warn "Telegram bridge not wired in this build — skipping wizard"
  fi
fi

# ---------- launchd ----------
if [ "$DO_LAUNCHD" = "1" ]; then
  step "Rendering + loading launchd plists"
  LA_DIR="$HOME/Library/LaunchAgents"
  mkdir -p "$LA_DIR"
  for TEMPLATE in "$INSTALL_DIR"/templates/launchd/*.plist.template; do
    [ -f "$TEMPLATE" ] || continue
    NAME=$(basename "$TEMPLATE" .plist.template)
    OUT="$LA_DIR/$NAME.plist"
    sed \
      -e "s|{{PYTHON}}|$VENV_DIR/bin/python3|g" \
      -e "s|{{INSTALL_DIR}}|$INSTALL_DIR|g" \
      -e "s|{{PROJECT_ROOT}}|$PROJECT_ROOT|g" \
      -e "s|{{PORT}}|$PORT|g" \
      -e "s|{{DEFAULT_MODEL}}|$MODEL|g" \
      "$TEMPLATE" > "$OUT"
    launchctl unload "$OUT" 2>/dev/null || true
    if launchctl load -w "$OUT" 2>/dev/null; then
      say "  loaded $NAME"
    else
      warn "launchctl load failed for $NAME (run manually: launchctl load -w $OUT)"
    fi
  done
else
  say "  skipping launchctl load (--no-launchd)"
fi

# ---------- start server ----------
if [ "$DO_START" = "1" ]; then
  step "Starting server"
  "$INSTALL_DIR/bin/cc" start || warn "start failed — check $INSTALL_DIR/logs"
  sleep 1
  "$INSTALL_DIR/bin/cc" status || true
else
  say "  skipping server start (--no-start)"
fi

# ---------- done ----------
printf "\n%s✓ install complete%s\n" "$GREEN" "$R"
cat <<NEXT

  install dir    $INSTALL_DIR
  dashboard      http://127.0.0.1:$PORT
  logs           $INSTALL_DIR/logs/
  shim           $INSTALL_DIR/bin/cc   (also in ~/.local/bin/cc if on PATH)

  next:
    cc doctor                    verify setup
    cc logs                      tail server logs
    cc trigger                   kick the dispatcher manually
    launchctl list | grep commandcentre    confirm launchd agents

  if you enabled OTEL, restart Claude Code to pick up the new settings.
NEXT
