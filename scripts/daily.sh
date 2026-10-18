#!/bin/bash
# UAE Job Copilot — daily runner
# Installed as launchd job, runs every day at 9:00 AM
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# launchd's PATH has no pyenv/homebrew — put the dependency-bearing python first
export PATH="${PYENV_ROOT:-$HOME/.pyenv}/shims:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
PY="$(command -v python3 || echo /usr/bin/python3)"

LOG="$ROOT/logs/daily-$(date +%Y%m%d).log"
mkdir -p "$ROOT/logs"

cd "$ROOT"

echo "=== Daily run started $(date) | python: $PY ($($PY --version 2>&1)) ===" >> "$LOG"
# launchd doesn't load .env — pull COPILOT_FLAGS from it if the shell doesn't have it
if [ -z "${COPILOT_FLAGS:-}" ] && [ -f "$ROOT/.env" ]; then
  COPILOT_FLAGS="$(sed -n 's/^COPILOT_FLAGS=//p' "$ROOT/.env" | tail -1 | tr -d '"')"
  export COPILOT_FLAGS
fi
$PY main.py run ${COPILOT_FLAGS:-} >> "$LOG" 2>&1
echo "=== Daily run finished $(date) ===" >> "$LOG"

# Keep only last 30 daily logs
ls -t "$ROOT"/logs/daily-*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
