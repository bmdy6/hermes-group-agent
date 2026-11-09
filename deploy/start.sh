#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${HERMES_HOME:?Set an absolute, isolated HERMES_HOME}"
export HERMES_HOME
: "${HERMES_REPO:?Set HERMES_REPO to the Hermes v0.23.0 checkout}"
PYTHON="$HERMES_REPO/.venv/bin/python"
[[ "$HERMES_HOME" = /* ]] || { echo 'Use an absolute, isolated HERMES_HOME' >&2; exit 1; }
HERMES_HOME=$("$PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' "$HERMES_HOME")
export HERMES_HOME
[[ "$HERMES_HOME" != / && "$HERMES_HOME" != "$(cd "$HOME" && pwd -P)" && "$HERMES_HOME" != "$(cd "$HOME" && pwd -P)/.hermes" ]] || { echo 'Use an isolated HERMES_HOME' >&2; exit 1; }
cd "$HERMES_HOME"
running=$("$HERMES_REPO/.venv/bin/python" -c 'from gateway.status import is_gateway_runtime_lock_active; print(is_gateway_runtime_lock_active())')
if [[ "$running" = True ]]; then
  echo 'Group gateway already running.'
  exit 0
fi
mkdir -p logs
nohup "$HERMES_REPO/.venv/bin/hermes" gateway run >> logs/gateway-nohup.log 2>&1 < /dev/null &
echo "Group gateway launched (PID $!); verify logs/gateway-nohup.log."
