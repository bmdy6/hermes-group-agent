#!/usr/bin/env bash
# Install locked Hermes dependencies and seed one isolated instance. Never starts a gateway.
set -euo pipefail
umask 077
: "${HERMES_HOME:?Set an absolute isolated instance directory}"
: "${HERMES_REPO:?Set an absolute path for the Hermes checkout}"
[[ "$HERMES_REPO" = /* && "$HERMES_HOME" = /* ]] || { echo 'Use absolute paths' >&2; exit 1; }
command -v uv >/dev/null || { echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' >&2; exit 1; }
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ! -e "$HERMES_REPO" ]]; then
  git clone --branch v0.23.0 --depth 1 https://github.com/NousResearch/hermes-agent.git "$HERMES_REPO"
fi
[[ "$(git -C "$HERMES_REPO" describe --tags --exact-match HEAD)" = v0.23.0 ]] || { echo 'Hermes v0.23.0 required' >&2; exit 1; }
(cd "$HERMES_REPO" && uv sync --frozen --python 3.14 --extra telegram --extra mcp)
export HERMES_REPO HERMES_HOME
bash "$ROOT/deploy/install.sh"
echo 'Initialization complete. Fill private credentials, run acceptance checks, then start the gateway explicitly.'
