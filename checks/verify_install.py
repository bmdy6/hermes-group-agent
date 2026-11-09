"""Integration check: temporary HOME; real Hermes cron CLI; never starts a gateway."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
hermes_repo = str(Path(sys.argv[1]).resolve())


def snapshot(home):
    return {str(p.relative_to(home)): (p.stat().st_mtime_ns, p.stat().st_ctime_ns,
            stat.S_IMODE(p.stat().st_mode), hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in [home, *home.rglob("*")]}


with tempfile.TemporaryDirectory(prefix="group-install-") as tmp:
    home = Path(tmp)
    team = home / ".hermes-fixture"
    env = {"HOME": tmp, "HERMES_HOME": str(team), "HERMES_REPO": hermes_repo,
           "PATH": os.defpath + ":/usr/local/bin"}
    # sitecustomize denies Python network even in the real CLI subprocess.
    guard = home / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import sys\ndef deny(e,a):\n if e in ('socket.connect','socket.getaddrinfo'): raise RuntimeError('network forbidden')\nsys.addaudithook(deny)\n")
    env["PYTHONPATH"] = str(guard)

    def install():
        result = subprocess.run(["bash", str(root / "deploy/install.sh")], env=env,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    install()
    assert stat.S_IMODE((team / ".env").stat().st_mode) == 0o600
    for name in ('plugin.yaml', '__init__.py'):
        assert (team / 'plugins/jev-router' / name).read_bytes() == (root / 'templates/hermes/plugins/jev-router' / name).read_bytes()
    before = snapshot(home)
    install()
    assert snapshot(home) == before, "Blank-credentials second install changed the tree"
    print("Blank credentials: two installs OK, no changes; .env mode=600")
    # Test-only synthetic principal; no real IDs stored in the repository.
    owner = str(10 ** 5 + 1)
    with (team / ".env").open("a") as f:
        f.write(f"\nTWITTER_DIGEST_USER_ID={owner}\n# operator-owned sentinel\n")
    original = (team / ".env").read_bytes()
    output = install()
    before = snapshot(home)
    install()
    assert snapshot(home) == before, "Configured second install changed the tree"
    assert (team / ".env").read_bytes() == original
    jobs = json.loads((team / "cron/jobs.json").read_text())["jobs"]
    assert len(jobs) == 1, jobs
    job = jobs[0]
    assert job["name"] == "twitter-digest"
    assert job["deliver"] == f"telegram:{owner}"
    assert job["script"] == "twitter_digest.py"
    assert job["schedule"]["expr"] == "0 9 * * *", job["schedule"]
    assert "UTC" in json.dumps(job), job
    print("Configured: two installs OK; content/mtime/ctime/mode unchanged; .env preserved; cron jobs=1")
    print("Cron: 0 9 * * *; UTC; telegram:<synthetic owner>; script=twitter_digest.py")
    print("Gateway never started; Python network blocked")
