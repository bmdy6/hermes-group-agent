"""Offline deployment contract: run the real shell scripts with an isolated CLI double."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class Deploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.instance = self.base / 'isolated-instance'
        self.repo = self.base / 'runtime'
        binary = self.repo / '.venv/bin'
        binary.mkdir(parents=True)
        (binary / 'python').symlink_to(sys.executable)
        self.env = {'HOME': str(self.base), 'HERMES_HOME': str(self.instance),
                    'HERMES_REPO': str(self.repo), 'PATH': os.defpath}
        for args in [('init',), ('-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                                  'commit','--allow-empty','-m','fixture'), ('tag','v0.23.0')]:
            subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)
        cli = binary / 'hermes'
        cli.write_text('#!' + sys.executable + '\n' + '''import json, os
from pathlib import Path
import sys
home = Path(os.environ['HERMES_HOME'])
if sys.argv[1:3] == ['cron','create']:
    p = home / 'cron/jobs.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    args = sys.argv
    p.write_text(json.dumps({'jobs':[{'name':args[args.index('--name')+1], 'deliver':args[args.index('--deliver')+1]}]}))
else:
    raise SystemExit('Unexpected CLI invocation')
''')
        cli.chmod(0o700)

    def run_script(self, script='install.sh', env=None):
        return subprocess.run(['bash', str(ROOT / 'deploy' / script)], env=self.env if env is None else env,
                              capture_output=True, text=True, timeout=15)

    def snapshot(self):
        return {str(p.relative_to(self.instance)): (p.stat().st_mtime_ns, stat.S_IMODE(p.stat().st_mode),
                    hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
                for p in [self.instance, *self.instance.rglob('*')]}

    def test_explicit_home_required(self):
        env = {k:v for k,v in self.env.items() if k != 'HERMES_HOME'}
        for script in ['install.sh', 'start.sh']:
            result = self.run_script(script, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('HERMES_HOME', result.stderr)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ['runtime'])

    def test_reject_shared_home(self):
        for target in [self.base / '.hermes', self.base, Path('/'), Path('relative'), self.base / 'child/../.hermes']:
            result = self.run_script(env={**self.env, 'HERMES_HOME':str(target)})
            self.assertNotEqual(result.returncode, 0)

    def test_install_twice_preserves_operator_files(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE((self.instance / '.env').stat().st_mode), 0o600)
        for path in ['SOUL.md','config.yaml','twitter/accounts.txt']:
            with (self.instance / path).open('a') as f:
                f.write('\n# operator customization\n')
        envfile = self.instance / '.env'
        with envfile.open('a') as f:
            f.write('\nTWITTER_DIGEST_USER_ID=' + str(10 ** 5 + 1) + '\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        for path in ['SOUL.md','config.yaml','twitter/accounts.txt']:
            self.assertIn('operator customization', (self.instance / path).read_text())
        self.assertEqual(len(json.loads((self.instance / 'cron/jobs.json').read_text())['jobs']), 1)

    def test_wrong_runtime_version_rejected(self):
        subprocess.run(['git','-C',str(self.repo),'tag','-d','v0.23.0'],check=True,capture_output=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.instance.exists())

    def test_start_checks_native_instance_lock(self):
        self.instance.mkdir()
        package = self.base / 'gateway'
        package.mkdir()
        (package/'__init__.py').touch()
        (package/'status.py').write_text('def is_gateway_runtime_lock_active(): return True\n')
        result = self.run_script('start.sh', {**self.env,'PYTHONPATH':str(self.base)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already running',result.stdout)
        self.assertFalse((self.instance/'logs').exists())
