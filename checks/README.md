# Explicit runtime checks

The default suite in `tests/` uses only the Python standard library and never starts a gateway. These additional scripts require a real, frozen Hermes v0.23.0 checkout with Python 3.14 and the telegram/mcp extras. Run from this repository root.

## Offline checks against real Hermes

Export the absolute checkout path as `HERMES_REPO`. No real credentials or Telegram identifiers are needed. `verify_install.py` creates a temporary HOME and HERMES_HOME, runs the actual cron CLI, blocks Python network calls, and verifies content, mode and timestamps across repeated installs:

```sh
python3 checks/verify_install.py "$HERMES_REPO"
PYTHONPATH="$HERMES_REPO" "$HERMES_REPO/.venv/bin/python" checks/verify_jev_hermes.py
```

For config, provider resolution and final tool-surface validation, create another disposable home with synthetic test values only:

```sh
check_tmp=$(mktemp -d)
export HOME="$check_tmp"
export HERMES_HOME="$check_tmp/instance"
bash deploy/install.sh
HERMES_HOME="$HERMES_HOME" python3 - <<'PY'
import os
from pathlib import Path
p = Path(os.environ['HERMES_HOME']) / '.env'
with p.open('a') as f:
    f.write('\nMODEL_API_KEY=fixture-offline\n')
    f.write('TELEGRAM_ALLOWED_USERS=fixture-owner\n')
    f.write('TELEGRAM_ALLOWED_CHATS=fixture-group\n')
    f.write('TELEGRAM_GROUP_ALLOWED_CHATS=fixture-group\n')
PY
PYTHONPATH="$HERMES_REPO" "$HERMES_REPO/.venv/bin/python" checks/verify_hermes.py
```

Use a subshell for this sequence so HOME changes remain local. Remove the disposable directory after reviewing results. These checks do not prove live group behavior or delivery.

## Optional live read-only MCP probe

`verify_mcp.py` is an explicit online probe, not part of the offline suite. Supply `GITHUB_READ_ONLY_TOKEN`, `LINEAR_READ_ONLY_TOKEN` and a non-sensitive `GITHUB_TEST_QUERY` from your secret store, without echoing credentials. Use credentials whose own permissions are read-only. It initializes both readonly endpoints, lists all tools, checks annotations and absence of write tools, and makes a safe read. It prints only boolean outcomes, suppressing response bodies and exception text. Do not run it with a write-capable token.

```sh
PYTHONPATH="$HERMES_REPO" "$HERMES_REPO/.venv/bin/python" checks/verify_mcp.py
```


Digest configuration and native command acceptance (offline, no gateway):

```sh
"$HERMES_REPO/.venv/bin/python" -m unittest discover -s tests -v
PYTHONPATH="$HERMES_REPO" "$HERMES_REPO/.venv/bin/python" checks/verify_follow_hermes.py
"$HERMES_REPO/.venv/bin/python" checks/verify_install.py "$HERMES_REPO"
```

The first command covers YAML migration/round-trip/concurrent edits, parsing/invalid handles/duplicate follows, mocked Apify budgets and queries, and shell installation contracts. The native check uses actual plugin discovery and context-aware dispatch, including different members of the same group, while denying network. Real delivery and model-format checks belong to live acceptance.
