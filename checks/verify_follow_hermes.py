"""Native v0.23.0 PluginManager + context dispatch, no network or gateway startup."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]

def deny(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}: raise RuntimeError('network forbidden')
sys.addaudithook(deny)
with tempfile.TemporaryDirectory(prefix='follow-runtime-') as tmp:
    home = Path(tmp)
    os.environ['HERMES_HOME'] = tmp
    os.environ['TELEGRAM_GROUP_ALLOWED_CHATS'] = '-fixture'
    shutil.copytree(ROOT/'templates/hermes/plugins', home/'plugins')
    shutil.copytree(ROOT/'templates/scripts', home/'scripts')
    shutil.copytree(ROOT/'templates/twitter', home/'twitter')
    shutil.copyfile(ROOT/'templates/hermes/config.yaml', home/'config.yaml')
    from hermes_cli.plugins import PluginManager
    from hermes_cli.plugin_command_dispatch import invoke_plugin_command
    manager = PluginManager(); manager.discover_and_load()
    assert {'follow', 'unfollow', 'following'} <= manager._plugin_commands.keys()
    entries = manager._plugin_commands
    commands = entries['follow']['handler'].func.__self__
    commands.lookup = Mock(return_value={'userName': 'fixture_user', 'name': 'Fixture', 'followers': 42})
    from gateway.config import Platform
    from gateway.session import SessionSource
    source = SessionSource(platform=Platform.TELEGRAM, chat_id='-fixture', chat_type='group', user_id='member')
    for command, args, expected in [('follow', 'fixture_user 测试', '已关注 Fixture'), ('following', '', 'fixture_user — 测试'), ('unfollow', 'fixture_user', '已取消')]:
        reply = invoke_plugin_command(entries[command]['handler'], args, source=source, platform='telegram')
        assert expected in reply, reply
    # No command-specific owner gate: another member of the same authorized group can edit.
    source.user_id = 'another-member'
    assert '已关注' in invoke_plugin_command(entries['follow']['handler'], 'fixture_user', source=source)
    assert '目标群' in invoke_plugin_command(entries['follow']['handler'], 'other', source=None)
    print('PASS native discovery, source-context dispatch, shared group-member commands, persistence; network blocked')
