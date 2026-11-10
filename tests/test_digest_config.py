import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'templates'
sys.path.insert(0, str(ROOT / 'scripts'))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'digest.yaml'

    def test_migration_keeps_comments_and_notes(self):
        from digest_config import initialize, load_config
        legacy = self.path.with_name('accounts.txt')
        legacy.write_text('# old\n@alice\nALICE\nbob\n')
        initialize(self.path, ROOT/'twitter/digest.yaml')
        self.assertFalse(legacy.exists())
        self.assertEqual(list(load_config(self.path)['accounts']), ['alice', 'bob'])
        self.assertIn('#', self.path.read_text())
        before = self.path.read_bytes()
        initialize(self.path, ROOT/'twitter/digest.yaml')
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_schema_rejected_before_migration_delete(self):
        from digest_config import initialize, load_config
        legacy = self.path.with_name('accounts.txt'); legacy.write_text('bad-name\n')
        with self.assertRaises(ValueError): initialize(self.path, ROOT/'twitter/digest.yaml')
        self.assertTrue(legacy.exists())
        self.assertFalse(self.path.exists())
        self.path.write_text('accounts: []\n')
        with self.assertRaises(ValueError): load_config(self.path)

    def test_roundtrip_comments_and_case_insensitive_duplicates(self):
        from digest_config import initialize, edit_accounts, load_config
        initialize(self.path, ROOT/'twitter/digest.yaml')
        self.path.write_text(self.path.read_text() + '\n# operator comment\n')
        edit_accounts(self.path, 'add', 'fixture_user', '备注: 保留 # 标点')
        with self.assertRaises(ValueError): edit_accounts(self.path, 'add', 'FIXTURE_USER', '')
        self.assertEqual(load_config(self.path)['accounts']['fixture_user'], '备注: 保留 # 标点')
        self.assertIn('# operator comment', self.path.read_text())
        edit_accounts(self.path, 'remove', 'FIXTURE_USER')
        self.assertNotIn('fixture_user', load_config(self.path)['accounts'])

    def test_empty_follow_list_valid(self):
        from digest_config import initialize, load_config, edit_accounts
        initialize(self.path, ROOT/'twitter/digest.yaml')
        for handle in list(load_config(self.path)['accounts']): edit_accounts(self.path, 'remove', handle)
        self.assertEqual(dict(load_config(self.path)['accounts']), {})


if __name__ == '__main__': unittest.main()

class ConcurrentConfigTests(unittest.TestCase):
    def test_parallel_writers_do_not_lose_updates(self):
        import concurrent.futures
        from digest_config import initialize, edit_accounts, load_config
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'digest.yaml'
            initialize(path, ROOT/'twitter/digest.yaml')
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(edit_accounts, path, 'add', f'fixture_{i}', 'note') for i in range(12)]
                for future in futures: future.result(timeout=10)
            self.assertTrue(all(f'fixture_{i}' in load_config(path)['accounts'] for i in range(12)))
