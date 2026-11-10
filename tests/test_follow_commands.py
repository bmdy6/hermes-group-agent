import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import sys
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1] / 'templates'
sys.path.insert(0, str(ROOT/'scripts'))
from digest_config import initialize, load_config


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        initialize(self.home/'twitter/digest.yaml', ROOT/'twitter/digest.yaml')
        spec = importlib.util.spec_from_file_location('follow_plugin', ROOT/'hermes/plugins/digest-follow/__init__.py')
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.lookup = Mock(return_value={'userName': 'fixture_user', 'name': 'Fixture', 'followers': 42})
        self.commands = self.m.Commands(self.home, lookup=self.lookup, env={'TELEGRAM_GROUP_ALLOWED_CHATS': '-fixture'})
        self.source = SimpleNamespace(chat_id='-fixture', chat_type='group', platform=SimpleNamespace(value='telegram'))

    def run_command(self, command, text='', source=True):
        return self.commands.run(command, text, source=self.source if source is True else source)

    def test_follow_note_duplicate_list_unfollow(self):
        self.assertEqual(self.run_command('follow', '@fixture_user 备注 # test'), '已关注 Fixture（42 粉丝）')
        self.assertIn('fixture_user — 备注 # test', self.run_command('following'))
        self.assertIn('已关注', self.run_command('follow', 'FIXTURE_USER'))
        self.lookup.assert_called_once()
        self.assertIn('已取消', self.run_command('unfollow', 'FIXTURE_USER'))
        self.assertNotIn('fixture_user', load_config(self.home/'twitter/digest.yaml')['accounts'])

    def test_invalid_parse_and_sources_no_lookup(self):
        before = (self.home/'twitter/digest.yaml').read_bytes()
        for text in ['', '../escape', 'a'*16, 'bad-name', 'a\nb']:
            self.assertIn('用法', self.run_command('follow', text))
        self.assertIn('用法', self.run_command('unfollow', 'alice extra'))
        self.assertIn('用法', self.run_command('following', 'extra'))
        for source in [None, SimpleNamespace(chat_id='other', chat_type='group', platform=SimpleNamespace(value='telegram')), SimpleNamespace(chat_id='-fixture', chat_type='dm', platform=SimpleNamespace(value='telegram'))]:
            self.assertIn('目标群', self.run_command('follow', 'fixture_user', source))
        self.lookup.assert_not_called()
        self.assertEqual(before, (self.home/'twitter/digest.yaml').read_bytes())

    def test_lookup_failure_or_wrong_user_never_writes(self):
        before = (self.home/'twitter/digest.yaml').read_bytes()
        for result in [None, {'userName': 'other', 'name': 'Other', 'followers': 1}]:
            self.lookup.return_value = result
            self.assertIn('验证', self.run_command('follow', 'fixture_user'))
        self.lookup.side_effect = RuntimeError('secret response must not leak')
        reply = self.run_command('follow', 'fixture_user')
        self.assertNotIn('secret', reply)
        self.assertEqual(before, (self.home/'twitter/digest.yaml').read_bytes())


if __name__ == '__main__': unittest.main()


class BrokenConfigTests(FollowTests):
    def test_broken_yaml_returns_fixed_error_without_throwing(self):
        (self.home/'twitter/digest.yaml').write_text('accounts: [\n')
        reply = self.run_command('following')
        self.assertIn('配置读取失败', reply)
        self.assertNotIn(str(self.home), reply)
        self.lookup.assert_not_called()
