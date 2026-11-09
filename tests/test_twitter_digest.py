import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "templates/scripts/twitter_digest.py"


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.accounts = self.home / "accounts.txt"
        self.accounts.write_text("# accounts\n@alice\n")
        self.now = datetime.now(timezone.utc)
        self.tweet = {"id": "new", "text": "hello", "url": "https://x.com/alice/status/new",
                      "createdAt": self.now.strftime("%a %b %d %H:%M:%S %z %Y"),
                      "author": {"userName": "alice"}}
        self.fixture = self.home / "fixture.json"
        self.env = {"HOME": str(self.home), "HERMES_HOME": str(self.home / ".hermes-fixture"),
                    "PATH": os.defpath}

    def http_error(self, *args):
        # Python 3.14 HTTPError owns a closeable response wrapper, even for fixtures.
        error = HTTPError(*args)
        self.addCleanup(error.close)
        return error

    def payload(self, tweets=None, **extra):
        return {"status": "success", "data": {"tweets": tweets if tweets is not None else [self.tweet]},
                "has_next_page": False, "next_cursor": "", **extra}

    def run_script(self, payload=None, fixture=True):
        args = [sys.executable, str(SCRIPT), "--accounts", str(self.accounts)]
        if fixture:
            self.fixture.write_text(json.dumps(payload or self.payload()))
            args += ["--fixture", str(self.fixture)]
        return subprocess.run(args, env=self.env, capture_output=True, text=True)

    def seen(self):
        return Path(self.env["HERMES_HOME"]) / "state/twitter_seen.json"

    def test_new_tweet_and_repeat(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["tweets"][0]["id"], "new")
        self.assertIn("new", json.loads(self.seen().read_text()))
        self.assertEqual(json.loads(self.run_script().stdout), {"wakeAgent": False})

    def test_old_and_future_tweets(self):
        for hours in (-25, 1):
            tweet = {**self.tweet, "createdAt": (self.now + timedelta(hours=hours)).strftime("%a %b %d %H:%M:%S %z %Y")}
            self.assertEqual(json.loads(self.run_script(self.payload([tweet])).stdout), {"wakeAgent": False})

    def test_retweet_skipped_but_quote_kept(self):
        rt = {**self.tweet, "id": "rt", "retweeted_tweet": {"id": "original"}}
        quote = {**self.tweet, "quoted_tweet": {"id": "quoted"}}
        result = self.run_script(self.payload([rt, quote]))
        self.assertEqual([t["id"] for t in json.loads(result.stdout)["tweets"]], ["new"])

    def test_text_only_retweet(self):
        result = self.run_script(self.payload([{**self.tweet, "text": "RT @alice: hello"}]))
        self.assertEqual(json.loads(result.stdout), {"wakeAgent": False})

    def test_empty(self):
        result = self.run_script(self.payload([]))
        self.assertEqual(result.stdout.strip().splitlines()[-1], '{"wakeAgent": false}')

    def test_missing_key_no_state(self):
        result = self.run_script(fixture=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("twitterapi_io.key", result.stderr)
        self.assertFalse(self.seen().exists())

    def test_api_error_no_state(self):
        result = self.run_script({"status": "error", "msg": "fixture error"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.seen().exists())

    def test_malformed_payload_no_state(self):
        result = self.run_script({"status": "success", "data": {}})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.seen().exists())

    def load_module(self):
        spec = importlib.util.spec_from_file_location("digest", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.MIN_INTERVAL = 0  # 测试里不等频率限制
        return module

    def test_invalid_page_preserves_existing_state(self):
        self.seen().parent.mkdir(parents=True)
        self.seen().write_text('{"previous": "kept"}\n')
        before = self.seen().read_bytes()
        result = self.run_script([self.payload(), {"status": "error"}])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.seen().read_bytes(), before)

    def test_installed_script_without_hermes_home(self):
        import shutil
        installed = self.home / "scripts/twitter_digest.py"
        installed.parent.mkdir()
        shutil.copyfile(SCRIPT, installed)
        self.fixture.write_text(json.dumps(self.payload()))
        result = subprocess.run([sys.executable, str(installed), "--fixture", str(self.fixture),
                                 "--accounts", str(self.accounts)],
                                env={"HOME": str(self.home), "PATH": os.defpath}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / "state/twitter_seen.json").exists())

    def test_http_auth_pagination_and_file_key_priority(self):
        module = self.load_module()
        home = Path(self.env["HERMES_HOME"])
        (home / "secrets").mkdir(parents=True)
        (home / "secrets/twitterapi_io.key").write_text("fixture-file-key\n")
        pages = [self.payload(has_next_page=True, next_cursor="page2"),
                 self.payload([{**self.tweet, "id": "second"}])]
        requests = []

        def replay(request, **kwargs):
            requests.append(request)
            return contextlib.closing(io.BytesIO(json.dumps(pages.pop(0)).encode()))

        with patch.dict(os.environ, {**self.env, "TWITTER_DIGEST_API_KEY": "fixture-env-key"}, clear=True), \
             patch.object(module, "urlopen", side_effect=replay), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(module.main(["--accounts", str(self.accounts)]), 0)
        self.assertEqual(len(json.loads(out.getvalue())["tweets"]), 2)
        self.assertEqual(requests[0].get_header("X-api-key"), "fixture-file-key")
        self.assertIn("cursor=page2", requests[1].full_url)

    def test_string_code_zero_is_success(self):
        module = self.load_module()
        page = {**self.payload(), "code": "0", "msg": "success"}

        def replay(request, **kwargs):
            return contextlib.closing(io.BytesIO(json.dumps(page).encode()))

        with patch.dict(os.environ, {**self.env, "TWITTER_DIGEST_API_KEY": "k"}, clear=True), \
             patch.object(module, "urlopen", side_effect=replay), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(module.main(["--accounts", str(self.accounts)]), 0)
        self.assertEqual(len(json.loads(out.getvalue())["tweets"]), 1)

    def test_rate_limited_once_then_retried(self):
        module = self.load_module()
        calls = []

        def replay(request, **kwargs):
            calls.append(request)
            if len(calls) == 1:
                raise self.http_error("https://api.twitterapi.io", 429, "rate", {}, io.BytesIO())
            return contextlib.closing(io.BytesIO(json.dumps(self.payload()).encode()))

        with patch.dict(os.environ, {**self.env, "TWITTER_DIGEST_API_KEY": "k"}, clear=True), \
             patch.object(module, "urlopen", side_effect=replay), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(module.main(["--accounts", str(self.accounts)]), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(json.loads(out.getvalue())["tweets"]), 1)

    def test_http_failure_redacted_no_state(self):
        module = self.load_module()
        with patch.dict(os.environ, {**self.env, "TWITTER_DIGEST_API_KEY": "private-fixture-key"}, clear=True), \
             patch.object(module, "urlopen", side_effect=self.http_error("https://api.twitterapi.io", 429, "secret", {}, io.BytesIO())), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(module.main(["--accounts", str(self.accounts)]), 1)
        self.assertNotIn("private-fixture-key", err.getvalue())
        self.assertFalse(self.seen().exists())

    def test_account_failures_do_not_stop_other_accounts(self):
        module = self.load_module()
        for failed in ("alice", "bob", "carol"):
            with self.subTest(failed=failed):
                self.accounts.write_text("alice\nbob\ncarol\n")
                self.seen().unlink(missing_ok=True)
                requested = []

                def replay(request, **kwargs):
                    from urllib.parse import parse_qs, urlparse
                    account = parse_qs(urlparse(request.full_url).query)["userName"][0]
                    requested.append(account)
                    if account == failed:
                        raise self.http_error(request.full_url, 500, "private-response-body", {}, io.BytesIO())
                    tweet = {**self.tweet, "id": account, "author": {"userName": account}}
                    return contextlib.closing(io.BytesIO(json.dumps(self.payload([tweet])).encode()))

                with patch.dict(os.environ, {**self.env, "TWITTER_DIGEST_API_KEY": "private-fixture-key"}, clear=True), \
                     patch.object(module, "urlopen", side_effect=replay), \
                     contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(module.main(["--accounts", str(self.accounts)]), 0)
                expected = [a for a in ("alice", "bob", "carol") if a != failed]
                self.assertEqual(requested, ["alice", "bob", "carol"])
                self.assertEqual([t["account"] for t in json.loads(out.getvalue())["tweets"]], expected)
                self.assertEqual(set(json.loads(self.seen().read_text())), set(expected))
                self.assertEqual(err.getvalue(), f"twitter_digest: @{failed}: HTTPError\n")

    def test_all_accounts_fail_preserves_state_and_redacts_errors(self):
        module = self.load_module()
        self.accounts.write_text("alice\nbob\n")
        self.seen().parent.mkdir(parents=True)
        self.seen().write_text(json.dumps({"previous": (self.now - timedelta(days=8)).isoformat()}))
        before = self.seen().read_bytes()
        pages = [{"status": "error", "msg": "private-response-body private-fixture-key"},
                 {"status": "success", "data": {}}]
        with patch.dict(os.environ, {**self.env, "TWITTER_DIGEST_API_KEY": "private-fixture-key"}, clear=True), \
             patch.object(module, "urlopen", side_effect=lambda *a, **k:
                          contextlib.closing(io.BytesIO(json.dumps(pages.pop(0)).encode()))), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(module.main(["--accounts", str(self.accounts)]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "twitter_digest: @alice: ValueError\ntwitter_digest: @bob: ValueError\n")
        self.assertEqual(self.seen().read_bytes(), before)

    def test_later_page_failure_skips_account_even_with_empty_success(self):
        module = self.load_module()
        self.accounts.write_text("alice\nbob\n")
        pages = [self.payload(has_next_page=True, next_cursor="page2"),
                 {"status": "error", "msg": "private-response-body"}, self.payload([])]
        with patch.dict(os.environ, {**self.env, "TWITTER_DIGEST_API_KEY": "private-fixture-key"}, clear=True), \
             patch.object(module, "urlopen", side_effect=lambda *a, **k:
                          contextlib.closing(io.BytesIO(json.dumps(pages.pop(0)).encode()))), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(module.main(["--accounts", str(self.accounts)]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"wakeAgent": False})
        self.assertEqual(err.getvalue(), "twitter_digest: @alice: ValueError\n")
        self.assertFalse(self.seen().exists())
        self.assertEqual(pages, [])

    def test_seen_retention_on_write_with_or_without_new_tweets(self):
        module = self.load_module()
        for tweets in ([self.tweet], []):
            with self.subTest(new_tweets=bool(tweets)):
                self.seen().parent.mkdir(parents=True, exist_ok=True)
                retained = {"boundary": (self.now - timedelta(days=7)).isoformat(),
                            "recent": (self.now - timedelta(days=1)).isoformat()}
                self.seen().write_text(json.dumps({**retained,
                    "expired": (self.now - timedelta(days=7, seconds=1)).isoformat()}))
                self.fixture.write_text(json.dumps(self.payload(tweets)))
                with patch.dict(os.environ, self.env, clear=True), \
                     patch.object(module, "datetime", wraps=datetime) as clock, \
                     contextlib.redirect_stdout(io.StringIO()) as out:
                    clock.now.return_value = self.now
                    self.assertEqual(module.main(["--accounts", str(self.accounts), "--fixture", str(self.fixture)]), 0)
                expected = {**retained, **({"new": self.now.isoformat()} if tweets else {})}
                self.assertEqual(json.loads(self.seen().read_text()), expected)
                if not tweets:
                    self.assertEqual(json.loads(out.getvalue()), {"wakeAgent": False})


if __name__ == "__main__":
    unittest.main()
