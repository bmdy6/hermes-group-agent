"""Real v0.23.0 discovery, adapter and SessionStore; no gateway or network.

Run with its .venv Python and PYTHONPATH pointing to the temporary Hermes tree.
"""
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

root = Path(__file__).resolve().parents[1]
logging.disable(logging.CRITICAL)


def deny_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('Offline acceptance forbids network')


sys.addaudithook(deny_network)

with tempfile.TemporaryDirectory(prefix='group-jev-hermes-') as tmp:
    os.environ['HERMES_HOME'] = tmp
    os.environ['TELEGRAM_GROUP_ALLOWED_CHATS'] = 'fixture-group'
    from hermes_yaml import safe_load, safe_dump
    config = safe_load((root / 'templates/hermes/config.yaml').read_text())
    home = Path(tmp)
    shutil.copytree(root / 'templates/hermes/plugins', home / 'plugins')
    shutil.copytree(root / 'templates/scripts', home / 'scripts')
    (home / 'config.yaml').write_text(safe_dump(config))
    from hermes_cli.plugins import PluginManager
    manager = PluginManager()
    manager.discover_and_load()
    assert not manager._hooks.get('pre_gateway_dispatch'), 'Default plugin must be disabled'
    config['plugins']['enabled'] = ['jev-router']
    config['plugins']['entries']['jev-router']['settings']['enabled'] = True
    config['platforms']['telegram']['require_mention'] = False
    (home / 'config.yaml').write_text(safe_dump(config))
    manager = PluginManager()
    manager.discover_and_load()
    assert len(manager._hooks.get('pre_gateway_dispatch', [])) == 1

    from gateway.config import GatewayConfig, PlatformConfig
    from gateway.platforms.event import MessageType
    from gateway.session import SessionStore
    from plugins.platforms.telegram.adapter import TelegramAdapter
    from telegram import Message, Chat, User, MessageEntity
    store = SessionStore(home / 'sessions', GatewayConfig())
    adapter = TelegramAdapter(PlatformConfig(enabled=True, extra={
        'require_mention': True, 'observe_unmentioned_group_messages': True,
        'observe_allowed_chats': ['fixture-group'], 'guest_mode': False}))
    # IDs are synthetic test-only values; neither values nor API objects are printed.
    ident = len('fixture')
    chat = Chat(-ident, 'group', title='Fixture')
    member = User(ident + 1, 'Member', False)
    bot = User(ident + 2, 'Assistant', True, username='fixture_bot')
    adapter._bot = SimpleNamespace(id=bot.id, username=bot.username)
    adapter._current_bot_username = lambda: bot.username
    adapter._chat_id_str = lambda raw: 'fixture-group'
    adapter._telegram_allowed_chats = lambda: {'fixture-group'}
    adapter._telegram_observe_allowed_chats = lambda: {'fixture-group'}
    adapter._is_user_authorized_from_message = lambda raw: True
    adapter._session_store = store
    # No live update ACK context in this offline harness.
    adapter._accept_update = lambda: None
    raw = Message(ident, datetime.now(timezone.utc), chat, member, text='Can the assistant help?')
    assert not adapter._should_process_message(raw)
    assert adapter._should_observe_unmentioned_group_message(raw)
    assert not adapter._gate_or_observe(raw, SimpleNamespace(update_id=ident), MessageType.TEXT)
    # Existing native observation is canonical and persists before gateway dispatch.
    event = adapter._apply_telegram_group_observe_attribution(adapter._build_message_event(raw, MessageType.TEXT))
    entry = store.get_or_create_session(event.source)
    initial = store.load_transcript(entry.session_id)
    assert initial and any('Can the assistant help?' in str(row.get('content')) for row in initial)
    adapter.config.extra['require_mention'] = False
    assert adapter._should_process_message(raw)
    assert not adapter._should_observe_unmentioned_group_message(raw)
    gateway = SimpleNamespace(_intake_adapter_for=lambda source: adapter,
                              _is_user_authorized_for_source=lambda source: True)
    callback = manager._hooks['pre_gateway_dispatch'][0]
    policy = callback.__self__
    policy.env = {'JEV_API_KEY': 'fixture', 'TELEGRAM_GROUP_ALLOWED_CHATS': str(chat.id)}
    policy.request = AsyncMock(return_value=[.1, .99, .99])

    adapter._message_mentions_bot(raw)
    adapter._is_reply_to_bot(raw)
    policy._settings()
    policy._context(event, store, 10)

    async def acceptance():
        # Route the real, attributed event through Hermes' actual hook dispatcher.
        event.message_id = 'fixture-rejected'
        event.text += ' rejected follow-up'
        results = await manager.ainvoke_hook('pre_gateway_dispatch', event=event, gateway=gateway, session_store=store)
        assert results[0]['action'] == 'skip'
        history = store.load_transcript(entry.session_id)
        assert any('rejected follow-up' in str(row.get('content')) for row in history), 'Rejected message must remain in the native conversation'
        policy.request.return_value = [.99, .1, .1]
        event.message_id = 'fixture-allowed'
        results = await manager.ainvoke_hook('pre_gateway_dispatch', event=event, gateway=gateway, session_store=store)
        assert not results
        assert event.reply_expected is True
        mention = Message(ident + 3, raw.date, chat, member, text='@fixture_bot help',
                          entities=[MessageEntity('mention', 0, len('@fixture_bot'))])
        direct = adapter._apply_telegram_group_observe_attribution(adapter._build_message_event(mention, MessageType.TEXT))
        before = policy.request.await_count
        results = await manager.ainvoke_hook('pre_gateway_dispatch', event=direct, gateway=gateway, session_store=store)
        assert not results and policy.request.await_count == before
        # Native attribution and bot-reply metadata are retained as well.
        reply = Message(ident + 4, raw.date, chat, member, text='help', reply_to_message=Message(ident + 5, raw.date, chat, bot, text='hello'))
        direct = adapter._apply_telegram_group_observe_attribution(adapter._build_message_event(reply, MessageType.TEXT))
        results = await manager.ainvoke_hook('pre_gateway_dispatch', event=direct, gateway=gateway, session_store=store)
        assert not results and policy.request.await_count == before

    asyncio.run(acceptance())
    print('Native discovery: default off, explicit enable registers exactly one ingress hook')
    print('Telegram: mention filter -> native observe/return -> gateway hook confirmed')
    print('Real SessionStore: rejection retained; high score allowed; mention/reply bypassed Jev')
    print('No gateway started; network denied; all state stayed inside temporary HERMES_HOME')
