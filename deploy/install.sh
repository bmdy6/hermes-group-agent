#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
: "${HERMES_HOME:?Set an absolute, isolated HERMES_HOME}"
export HERMES_HOME
: "${HERMES_REPO:?Set HERMES_REPO to the Hermes v0.23.0 checkout}"
HERMES="$HERMES_REPO/.venv/bin/hermes"
PYTHON="$HERMES_REPO/.venv/bin/python"
[[ "$HERMES_HOME" = /* ]] || { echo 'Use an absolute, isolated HERMES_HOME' >&2; exit 1; }
HERMES_HOME=$("$PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' "$HERMES_HOME")
export HERMES_HOME
[[ "$HERMES_HOME" != / && "$HERMES_HOME" != "$(cd "$HOME" && pwd -P)" && "$HERMES_HOME" != "$(cd "$HOME" && pwd -P)/.hermes" ]] || { echo 'Use an isolated HERMES_HOME' >&2; exit 1; }
[[ -x "$HERMES" && -x "$PYTHON" ]] || { echo 'Hermes virtualenv is missing' >&2; exit 1; }
[[ "$(git -C "$HERMES_REPO" describe --tags --exact-match HEAD)" = v0.23.0 ]] || { echo 'Hermes v0.23.0 required' >&2; exit 1; }
mkdir -p "$HERMES_HOME"/{secrets,state,scripts,logs,twitter}
copy() { cmp -s "$1" "$2" || cp "$1" "$2"; }
seed() { if [[ ! -e "$2" ]]; then cp "$1" "$2"; fi; }
seed "$ROOT/templates/hermes/config.yaml" "$HERMES_HOME/config.yaml"
seed "$ROOT/templates/hermes/SOUL.md" "$HERMES_HOME/SOUL.md"
for plugin in jev-router digest-follow; do
  mkdir -p "$HERMES_HOME/plugins/$plugin"
  for name in plugin.yaml __init__.py; do
    copy "$ROOT/templates/hermes/plugins/$plugin/$name" "$HERMES_HOME/plugins/$plugin/$name"
  done
done
for name in twitter_digest.py digest_config.py twitter_profile.py; do
  copy "$ROOT/templates/scripts/$name" "$HERMES_HOME/scripts/$name"
done
# Keep operator/command edits. Legacy accounts migrate before the old file is deleted.
copy "$ROOT/templates/twitter/digest.yaml" "$HERMES_HOME/twitter/digest.defaults.yaml"
"$PYTHON" "$HERMES_HOME/scripts/digest_config.py" "$HERMES_HOME/twitter/digest.yaml" "$HERMES_HOME/twitter/digest.defaults.yaml"
copy "$ROOT/deploy/start.sh" "$HERMES_HOME/start.sh"
if [[ ! -e "$HERMES_HOME/.env" ]]; then
  cp "$ROOT/templates/.env.example" "$HERMES_HOME/.env"
  chmod 600 "$HERMES_HOME/.env"
fi
set -a
source "$HERMES_HOME/.env"
set +a
export HERMES_HOME
if [[ -n "${TWITTER_DIGEST_USER_ID:-}" ]]; then
  [[ "$TWITTER_DIGEST_USER_ID" =~ ^-?[0-9]+$ ]] || { echo 'TWITTER_DIGEST_USER_ID must be numeric' >&2; exit 1; }
  exists=$("$PYTHON" - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ['HERMES_HOME']) / 'cron/jobs.json'
jobs = json.loads(p.read_text())['jobs'] if p.exists() else []
print(any(j.get('name') == 'twitter-digest' for j in jobs))
PY
)
  if [[ "$exists" = False ]]; then
    error_owner="${TWITTER_DIGEST_ERROR_USER_ID:-}"
    if [[ -z "$error_owner" && "$TWITTER_DIGEST_USER_ID" =~ ^[0-9]+$ ]]; then error_owner="$TWITTER_DIGEST_USER_ID"; fi
    [[ "$error_owner" =~ ^[0-9]+$ ]] || { echo 'Set positive TWITTER_DIGEST_ERROR_USER_ID for private failure notices' >&2; exit 1; }
    "$HERMES" cron create "${TWITTER_DIGEST_SCHEDULE:-0 9 * * *}" "$(cat "$ROOT/templates/cron/twitter_digest.md")" \
      --name twitter-digest --timezone "${TWITTER_DIGEST_TIMEZONE:-UTC}" \
      --script twitter_digest.py --deliver "telegram:$TWITTER_DIGEST_USER_ID" \
      --failure-deliver "telegram:$error_owner"
  fi
else
  echo 'Fill TWITTER_DIGEST_USER_ID in .env, then rerun install.sh to create the cron job.'
fi
echo "Next: fill $HERMES_HOME/.env; write secrets/apify.key (chmod 600); review twitter/digest.yaml."
echo "Then run: HERMES_HOME='$HERMES_HOME' HERMES_REPO='$HERMES_REPO' bash '$HERMES_HOME/start.sh'; use deploy/keepalive.snippet if needed."
