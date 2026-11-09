"""Run with the isolated v0.23.0 Python and temporary HOME/HERMES_HOME. No gateway."""
import json
import os
from pathlib import Path
import sys

home = Path(os.environ["HERMES_HOME"]).resolve()
assert home != Path.home() / ".hermes", "Use an isolated home"


def no_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("Network is forbidden during acceptance")


sys.addaudithook(no_network)
from dotenv import load_dotenv
load_dotenv(home / ".env", override=True)
from hermes_cli.config import load_config, validate_config_structure, _validate_config_key
from hermes_yaml import safe_load

raw = safe_load((home / "config.yaml").read_text())


def leaves(value, prefix=""):
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict) and item:
            yield from leaves(item, path)
        else:
            yield path


unknown = [key for key in leaves(raw) if not _validate_config_key(key)[0]]
assert not unknown, unknown
cfg = load_config()
assert not validate_config_structure(cfg), validate_config_structure(cfg)
print("Config: all leaf keys recognized; structure issues=[]")

from hermes_cli.runtime_provider import resolve_runtime_provider
runtime = resolve_runtime_provider(requested=cfg["model"]["provider"], target_model=cfg["model"]["default"])
assert runtime["base_url"] == "https://api.openai.com/v1", runtime["base_url"]
assert runtime["api_mode"] == "codex_responses", runtime["api_mode"]
assert runtime["api_key"] == os.environ["MODEL_API_KEY"]
print("Model: gpt-6.1-terra; official OpenAI; codex_responses; key_env resolved")

from gateway.config import load_gateway_config, Platform
gw = load_gateway_config()
telegram = gw.platforms[Platform.TELEGRAM]
assert telegram.extra["require_mention"] is True
assert telegram.extra["observe_unmentioned_group_messages"] is True
assert telegram.extra["guest_mode"] is False
assert telegram.extra["drop_pending_on_cold_boot"] is False
from plugins.platforms.telegram.adapter import TelegramAdapter
adapter = TelegramAdapter(telegram)
assert adapter._telegram_require_mention()
assert adapter._telegram_observe_unmentioned_group_messages()
assert not adapter._telegram_guest_mode()
assert adapter._telegram_allowed_chats() == set(os.environ["TELEGRAM_ALLOWED_CHATS"].split(","))
assert adapter._telegram_observe_allowed_chats() == adapter._telegram_allowed_chats()
assert os.environ["TELEGRAM_ALLOWED_USERS"].strip()
print("Telegram: mention/observe=True; guest/drop_pending=False; .env chat allowlists resolved")

from hermes_cli.tools_config import _get_platform_tools
from hermes_cli.toolset_validation import validate_platform_toolsets, saved_toolset_resolver
warnings = validate_platform_toolsets(cfg["platform_toolsets"], saved_toolset_resolver(cfg))
assert all("platform 'cron' is configured with an empty toolset list" in w for w in warnings), warnings
enabled = sorted(_get_platform_tools(cfg, "telegram"))
assert set(enabled) == {"web", "skills", "todo", "memory", "session_search"}, enabled
assert _get_platform_tools(cfg, "cron") == set()
from toolsets import resolve_toolset
expanded = sorted({tool for ts in enabled for tool in resolve_toolset(ts)})
from model_tools import get_tool_definitions
raw_schemas = get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True, skip_tool_search_assembly=True)
catalog = sorted(schema["function"]["name"] for schema in raw_schemas)
schemas = get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)
names = sorted(schema["function"]["name"] for schema in schemas)
forbidden = {"terminal", "process", "read_file", "write_file", "patch", "search", "browser_exec",
             "browser_cdp", "browser_dialog", "browser_vault", "execute_code", "code_execution",
             "cronjob", "image_generate", "delegate_task"}
assert not forbidden.intersection(expanded + catalog + names), (expanded, catalog, names)
assert names, "Expected a non-empty tool surface"
from model_tools import _dispatch_bridge_tool
for tool in forbidden:
    result, dispatch = _dispatch_bridge_tool("tool_call", {"name": tool, "arguments": {}}, enabled, None)
    assert result and dispatch is None, (tool, result, dispatch)
assert all(not entry["enabled"] for entry in cfg["mcp_servers"].values())
print("Telegram toolsets:", json.dumps(enabled))
print("Expanded tools:", json.dumps(expanded))
print("Bridge catalog:", json.dumps(catalog))
print("Final available schemas:", json.dumps(names))
print("Forbidden intersection: []; MCP disabled; cron tools=[]; network blocked")
print("Tool Search bridge rejects every forbidden tool before dispatch")

from cron.scheduler_prompt import _parse_wake_gate
assert not _parse_wake_gate('{"wakeAgent": false}\n')
assert _parse_wake_gate('{"tweets": [{"id": "fixture"}]}\n')
print("Native cron wake gate: empty result skips agent; candidates wake agent")
