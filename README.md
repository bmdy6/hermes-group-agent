# Telegram group assistant

Build one isolated Hermes assistant for one Telegram group, with shared conversation recall and scoped read-only tools.

[简体中文](README.zh-CN.md) · [MIT](LICENSE) · A reusable agent skill with deployable templates.

## Who this is for

Small groups that want to ask about recent discussions, query approved repositories or work items, and optionally receive a daily Twitter digest. A deployment operator needs a Telegram account, a bot, a model API account, and a host for Hermes. Every member of the allowed group can query connected data; use a separate instance for private conversations.

## See a result in three minutes

With authenticated `gh` and Python 3.13+ already installed:

```sh
gh repo clone bmdy6/skill-tg-group-assistant
cd skill-tg-group-assistant
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

You will see offline router, digest, and deployment checks pass. No bot, credentials, gateway, or external API is needed for this preview. It is a local check, not a live Telegram setup. This private preview requires repository access.

## Install

For a skill-capable agent, clone into its supported skill directory, then ask:

> Use tg-group-assistant to set up a new Telegram group assistant. Read SKILL.md first.

The agent follows [SKILL.md](SKILL.md), the [interview](references/interview.md), and the [Telegram setup checklist](references/telegram-setup-task.md). Use a browser automation tool (for example ego-browser or Playwright), or configure the bot manually. Disable bot privacy mode **before** adding it to the group.

For the runtime, use **Hermes v0.23.0 with Python 3.14**, installed from its frozen lockfile:

```sh
git clone --branch v0.23.0 --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent
cd hermes-agent
uv sync --frozen --python 3.14 --extra telegram --extra mcp
cd ..
```

From this skill repository, point both variables at your own absolute paths:

```sh
export HERMES_REPO="$(cd ../hermes-agent && pwd)"
export HERMES_HOME="$HOME/.hermes-reading-group"
bash deploy/install.sh
```

The home must be isolated from your personal Hermes instance. Fill `$HERMES_HOME/.env` privately from your secret store; keep mode `600`. Set the target group in both chat allowlists, and the DM owner in the user allowlist. Customize the installed `config.yaml` and `SOUL.md`, then start:

```sh
bash "$HERMES_HOME/start.sh"
```

Installation does not start the gateway. Reinstalling preserves `.env`, config, personality, and account-list edits; it updates packaged scripts and plugin code. Config changes in templates are not applied over an existing instance: edit the installed config explicitly. Each group needs its own bot and `HERMES_HOME`.

MCP servers and Jev are disabled by default. For MCP, use a scope-limited **read-only token plus a read-only endpoint**, inspect the final tool list, and explicitly enable only the required server. Telegram tools are limited to web, skills, todo, memory, session search, and approved read-only MCP toolsets.

## Optional capabilities

| Capability | Configure |
|---|---|
| Twitter digest | Put public handles in `$HERMES_HOME/twitter/accounts.txt`; key in `secrets/twitterapi_io.key` with mode `600`; recipient, schedule and timezone in `.env`; rerun install. Existing jobs require an explicit cron CLI update. |
| Jev ambient routing | Disclose external context processing; stop the gateway; enable both plugin switches and set `require_mention: false` together; add the key privately. See [routing and rollback](SKILL.md#可选-jev-路由). |
| Keepalive | If systemd is unavailable, use the foreground loop in [keepalive.snippet](deploy/keepalive.snippet), plus a host boot hook. |

Jev uses an addressed score of at least **0.85**, a **600-second** per-group cooldown, **10** recent messages by default, and a **2-second** total timeout. Missing keys and errors stay silent. The other two scores are diagnostic. Review `scores` and `decision` in `$HERMES_HOME/state/jev_router.jsonl`; `allow` is a routing decision, not delivery proof. Logs use hashed references and redacted previews, retaining fourteen days.

## Three acceptance levels

| Level | Evidence |
|---|---|
| Code complete | Offline suite passes, isolated installation is idempotent, real Hermes config/tool-surface checks pass. See [runtime checks](checks/README.md). |
| Live integration passed | Silence without mention; attributed recall after mention; restart persistence; rejection in other groups; read-only MCP behavior; optional cron delivery recorded as `delivered`; Jev cooldown and failure behavior verified. |
| Running in production | At least 24 hours under supervision, scheduled delivery on time, and no recurring errors. |

A green offline suite proves the first local checks only. Runtime checks, live integration and uptime require separate evidence.

## Known pitfalls

- Hermes v0.23.0 needs Python 3.14 and `uv sync --frozen`; a working version command does not prove complete dependencies on Python 3.13.
- Disable privacy mode before joining. Bots cannot read group history from before they joined.
- Keep `drop_pending_on_cold_boot: false` so restarts retain pending updates. Stop using `getUpdates` after the gateway starts: a token supports one update consumer.
- Long-term memory is shared across the entire instance, including DMs. A prompt cannot make private data safe in a shared instance.
- Cron scripts cannot rely on environment variables. The digest derives home from its installed location and reads its key file first.
- twitterapi.io trial accounts are rate limited; requests are spaced by at least 5.5 seconds, with one retry on HTTP 429. The API `code` field can be a string; `"0"` is success.
- Digest deduplication records handoff to Hermes, not successful Telegram delivery. A rerun after a delivery failure may skip already handed-off candidates.
- `pgrep -f` may match the checking command itself. Startup uses Hermes' per-instance runtime lock; the keepalive loop also needs a boot hook.
- Jev requires `in_process` isolation and the native gateway/session store. Plugin config uses `jev_model` because `model` is a reserved root name.

## Layout

`SKILL.md` and `references/` guide setup. `templates/` contains the Hermes config, personality, native Jev plugin, digest script/prompt, blank account list and `.env.example`. `deploy/` installs and starts one instance. `tests/` is offline; `checks/` contains explicit real-runtime and live read-only probes.

## License

[MIT](LICENSE).
