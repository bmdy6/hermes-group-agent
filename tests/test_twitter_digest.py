import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1] / 'templates'
SCRIPT = ROOT / 'scripts/twitter_digest.py'
sys.path.insert(0, str(ROOT/'scripts'))
from digest_config import initialize, load_config, save


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.accounts = self.home / 'digest.yaml'
        legacy = self.home/'accounts.txt'
        legacy.write_text('# handles\n@alice\nalice\nbob\n')
        initialize(self.accounts, ROOT/'twitter/digest.yaml')
        self.now = datetime.now(timezone.utc)
        self.tweet = {'id': '123', 'text': 'hello', 'url': 'https://x.com/alice/status/123',
                      'createdAt': self.now.strftime('%a %b %d %H:%M:%S %z %Y'),
                      'author': {'userName': 'alice', 'name': 'Alice'}, 'likeCount': 4000, 'lang': 'en'}
        self.env = {'HERMES_HOME': str(self.home), 'PATH': os.defpath, 'APIFY_TOKEN': 'test-env-key'}
        spec = importlib.util.spec_from_file_location('digest', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        price_check = patch.object(self.m, 'check_price')
        price_check.start()
        self.addCleanup(price_check.stop)
        self.out, self.err = io.StringIO(), io.StringIO()

    def batch(self, tweets=None, source='follow', **extra):
        return {'source': source, 'items': [self.tweet] if tweets is None else tweets, **extra}

    def fixture(self, batches=None, extra=()):
        path = self.home / 'fixture.json'
        path.write_text(json.dumps(batches if batches is not None else [self.batch()]))
        return self.main(['--fixture', str(path), *extra])

    def main(self, extra=()):
        with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return self.m.main(['--config', str(self.accounts), *extra])

    def state(self, name):
        return self.home / 'state' / name

    def test_new_source_display_name_and_repeat(self):
        self.assertEqual(self.fixture(), 0, self.err.getvalue())
        t = json.loads(self.out.getvalue())['tweets'][0]
        self.assertEqual((t['source'], t['displayName']), ('follow', 'Alice'))
        self.out.seek(0); self.out.truncate()
        self.assertEqual(self.fixture(), 0)
        self.assertEqual(json.loads(self.out.getvalue()), {'wakeAgent': False})

    def test_old_future_retweet_reply_filtered_quote_kept(self):
        bad = [{**self.tweet, 'createdAt': (self.now + timedelta(hours=h)).isoformat()} for h in [-25, 1]]
        bad += [{**self.tweet, **x} for x in [{'isRetweet': True}, {'text': 'RT @a: hi'}, {'isReply': True}]]
        self.assertEqual(self.fixture([self.batch(bad + [{**self.tweet, 'quoted_tweet': {'id': 'q'}}])]), 0)
        self.assertEqual(len(json.loads(self.out.getvalue())['tweets']), 1)

    def test_hot_threshold_language_sort_and_top20(self):
        tweets = [{**self.tweet, 'id': str(i), 'likeCount': 3000 + i} for i in range(30)]
        tweets += [{**self.tweet, 'id': 'low', 'likeCount': 2999}, {**self.tweet, 'id': 'cn', 'lang': 'zh'}]
        self.assertEqual(self.fixture([self.batch(tweets, 'hot', lang='en', min_likes=3000, limit=20)]), 0)
        found = json.loads(self.out.getvalue())['tweets']
        self.assertEqual(len(found), 20)
        self.assertEqual([t['id'] for t in found], [str(i) for i in range(29, 9, -1)])

    def test_chinese_threshold_and_cross_source_dedup_follow_wins(self):
        cn = {**self.tweet, 'id': 'cn', 'lang': 'zh', 'likeCount': 300}
        self.assertEqual(self.fixture([self.batch(source='hot', lang='en', min_likes=3000),
                                      self.batch(), self.batch([cn], 'hot', lang='zh', min_likes=300)]), 0)
        tweets = json.loads(self.out.getvalue())['tweets']
        self.assertEqual({t['id']: t['source'] for t in tweets}, {'123': 'follow', 'cn': 'hot'})

    def test_empty(self):
        self.assertEqual(self.fixture([self.batch([])]), 0)
        self.assertEqual(json.loads(self.out.getvalue()), {'wakeAgent': False})

    def test_malformed_fixture_no_seen(self):
        for batches in [[{'source': 'follow', 'items': {}}], [{'source': 'other', 'items': []}]]:
            self.assertEqual(self.fixture(batches), 1)
            self.assertFalse(self.state('twitter_seen.json').exists())

    def test_invalid_seen_preserved(self):
        p = self.state('twitter_seen.json'); p.parent.mkdir(); p.write_text('{"previous":"bad"}')
        self.assertEqual(self.fixture(), 1)
        self.assertEqual(p.read_text(), '{"previous":"bad"}')

    def test_seen_retention_empty_and_new(self):
        p = self.state('twitter_seen.json'); p.parent.mkdir()
        p.write_text(json.dumps({'old': (self.now-timedelta(days=8)).isoformat(), 'recent': self.now.isoformat()}))
        self.assertEqual(self.fixture([self.batch([])]), 0)
        self.assertEqual(set(json.loads(p.read_text())), {'recent'})

    def test_dry_run_does_not_touch_seen(self):
        self.assertEqual(self.fixture(extra=['--dry-run']), 0)
        self.assertFalse(self.state('twitter_seen.json').exists())
        self.assertEqual(json.loads(self.out.getvalue())['selected'], 1)

    def test_installed_path_without_hermes_home(self):
        installed = self.home / 'scripts/twitter_digest.py'; installed.parent.mkdir(); shutil.copyfile(SCRIPT, installed)
        shutil.copyfile(ROOT/'scripts/digest_config.py', installed.parent/'digest_config.py')
        fixture = self.home / 'input.json'; fixture.write_text(json.dumps([self.batch()]))
        r = subprocess.run([sys.executable, str(installed), '--config', str(self.accounts), '--fixture', str(fixture)],
                           env={'PATH': os.defpath}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.state('twitter_seen.json').exists())

    def replay(self, request, **kwargs):
        self.requests.append(request)
        if request.get_method() == 'POST':
            self.run_no += 1
            data = {'data': {'id': f'run{self.run_no}', 'status': 'SUCCEEDED', 'defaultDatasetId': 'dataset',
                             'usageTotalUsd': 0.00025, 'usage': {}, 'chargedEventCounts': {'tweet': 1}}}
        else:
            data = [self.tweet]
        return contextlib.closing(io.BytesIO(json.dumps(data).encode()))

    def live(self, extra=()):
        self.requests, self.run_no = [], 0
        with patch.object(self.m, 'urlopen', side_effect=self.replay):
            return self.main(extra)

    def test_combined_queries_auth_budget_and_file_priority(self):
        (self.home/'secrets').mkdir(); (self.home/'secrets/apify.key').write_text('test-file-key\n')
        self.assertEqual(self.live(['--dry-run']), 0, self.err.getvalue())
        posts = [r for r in self.requests if r.get_method() == 'POST']
        self.assertEqual(len(posts), 3)
        payloads = [json.loads(r.data) for r in posts]
        self.assertIn('(from:alice OR from:bob)', payloads[0]['twitterContent'])
        self.assertIn('since_time:', payloads[0]['twitterContent'])
        self.assertIn('until_time:', payloads[0]['twitterContent'])
        self.assertIn('min_faves:3000 lang:en', payloads[1]['twitterContent'])
        self.assertIn('min_faves:300 lang:zh', payloads[2]['twitterContent'])
        self.assertEqual([p['queryType'] for p in payloads], ['Latest', 'Top', 'Top'])
        self.assertTrue(all(r.get_header('Authorization') == 'Bearer test-file-key' for r in self.requests))
        self.assertTrue(all('test-file-key' not in r.full_url for r in self.requests))
        self.assertAlmostEqual(sum(float(parse_qs(urlparse(r.full_url).query)['maxTotalChargeUsd'][0]) for r in posts), 0.075)
        self.assertEqual(json.loads(self.state('twitter_budget.json').read_text())['items'], 300)
        self.assertFalse(self.state('twitter_seen.json').exists())
        self.assertEqual(self.live(), 0)
        self.assertEqual(self.requests, [])

    def test_limits_configurable_and_day_reset(self):
        config = load_config(self.accounts)
        config['daily'].update(max_items=40, max_calls=2)
        for route in config['searches']: route['limit'] = 5
        save(self.accounts, config)
        self.assertEqual(self.live(), 0)
        posts = [r for r in self.requests if r.get_method() == 'POST']
        self.assertLessEqual(len(posts), 2)
        self.assertLessEqual(json.loads(self.state('twitter_budget.json').read_text())['items'], 40)
        b = json.loads(self.state('twitter_budget.json').read_text()); b['date'] = '2000-01-01'
        self.state('twitter_budget.json').write_text(json.dumps(b))
        self.assertEqual(self.live(), 0)
        self.assertGreater(len(self.requests), 0)

    def test_failures_charge_reservation_no_retry_redact_and_preserve_seen(self):
        self.state('twitter_seen.json').parent.mkdir()
        self.state('twitter_seen.json').write_text('{}')
        e = HTTPError('https://example.invalid/secret', 500, 'test-env-key', {}, io.BytesIO())
        self.addCleanup(e.close)
        with patch.object(self.m, 'urlopen', side_effect=e) as request:
            self.assertEqual(self.main(), 1)
        self.assertEqual(request.call_count, 3)
        self.assertNotIn('test-env-key', self.err.getvalue())
        self.assertNotIn('secret', self.err.getvalue())
        self.assertEqual(self.state('twitter_seen.json').read_text(), '{}')
        self.assertEqual(json.loads(self.state('twitter_budget.json').read_text())['calls'], 3)

    def test_one_query_failure_others_continue(self):
        real = self.replay
        def failing(request, **kwargs):
            if request.get_method() == 'POST' and self.run_no == 0:
                self.run_no += 1
                raise ValueError('test-env-key')
            return real(request, **kwargs)
        self.replay = failing
        self.assertEqual(self.live(), 0, self.err.getvalue())
        self.assertEqual(self.run_no, 3)
        self.assertNotIn('test-env-key', self.err.getvalue())

    def test_missing_key_and_bad_config_no_calls(self):
        del self.env['APIFY_TOKEN']
        with patch.object(self.m, 'urlopen') as request:
            self.assertEqual(self.main(), 1)
            self.assertIn('apify.key', self.err.getvalue())
            request.assert_not_called()
        self.env['APIFY_TOKEN'] = 'key'
        config = load_config(self.accounts)
        config['daily']['max_items'] = -1
        from digest_config import yaml
        with self.accounts.open('w') as f: yaml().dump(config, f)
        self.assertEqual(self.main(), 1)

    def test_overreturn_truncated_and_budget_not_refunded(self):
        config = load_config(self.accounts)
        config['daily']['max_items'] = 5
        config['searches'] = []
        save(self.accounts, config)
        def actor(*args, **kwargs):
            return [{**self.tweet, 'id': str(i)} for i in range(30)], {'id': 'x', 'usageTotalUsd': .00125}
        with patch.object(self.m, 'fetch', side_effect=actor):
            self.assertEqual(self.main(), 0)
        self.assertEqual(len(json.loads(self.out.getvalue())['tweets']), 5)
        self.assertEqual(json.loads(self.state('twitter_budget.json').read_text())['items'], 5)

    def test_polling_failed_run_and_timeout(self):
        def response(d): return contextlib.closing(io.BytesIO(json.dumps(d).encode()))
        with patch.object(self.m, 'urlopen', side_effect=[response({'data': {'id':'r','status':'RUNNING'}}),
                response({'data': {'id':'r','status':'SUCCEEDED','defaultDatasetId':'d','usageTotalUsd':0}}), response([])]):
            items, run = self.m.fetch('key', {'query':'AI', 'sort':'Top'}, 20)
        self.assertEqual(items, [])
        with patch.object(self.m, 'urlopen', return_value=response({'data': {'id':'r','status':'FAILED'}})):
            with self.assertRaises(ValueError): self.m.fetch('key', {'query':'AI','sort':'Top'},20)

    def test_polling_exhaustion_aborts_without_second_launch(self):
        calls = []
        def replay(path, key, payload=None):
            calls.append((path, payload))
            return {'data': {'id': 'slow', 'status': 'RUNNING'}}
        with patch.object(self.m, 'api', side_effect=replay):
            with self.assertRaises(ValueError):
                self.m.fetch('key', {'query': 'AI', 'sort': 'Top'}, 20)
        self.assertEqual(sum('/acts/' in path and payload is not None for path, payload in calls), 1)
        self.assertEqual(calls[-1], ('/actor-runs/slow/abort', {}))

    def test_actor_mock_tweet_skipped_without_losing_real_tweets(self):
        items = [self.tweet, {'type': 'mock_tweet', 'id': -1, 'text': 'minimum charge notice'}]
        self.assertEqual(self.fixture([self.batch(items)]), 0)
        self.assertEqual(len(json.loads(self.out.getvalue())['tweets']), 1)

    def test_concurrent_run_blocked_before_network(self):
        import fcntl
        self.state('twitter_digest.lock').parent.mkdir()
        with self.state('twitter_digest.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(self.m, 'urlopen') as request:
                self.assertEqual(self.main(), 1)
                request.assert_not_called()

    def test_pricing_guard_rejects_higher_price_extra_events_and_legacy_model(self):
        spec = importlib.util.spec_from_file_location('fresh', SCRIPT)
        fresh = importlib.util.module_from_spec(spec); spec.loader.exec_module(fresh)
        event = {'eventTieredPricingUsd': {'FREE': {'tieredEventPriceUsd': .00025}}}
        pricing = {'startedAt': '2020-01-01T00:00:00Z', 'pricingModel': 'PAY_PER_EVENT',
                   'pricingPerEvent': {'actorChargeEvents': {'apify-default-dataset-item': event}}}
        with patch.object(fresh, 'api', return_value={'data': {'pricingInfos': [pricing]}}):
            fresh.check_price('key')
            event['eventTieredPricingUsd']['FREE']['tieredEventPriceUsd'] = .001
            with self.assertRaises(ValueError): fresh.check_price('key')
            event['eventTieredPricingUsd']['FREE']['tieredEventPriceUsd'] = .00025
            pricing['pricingPerEvent']['actorChargeEvents']['start'] = {'eventPriceUsd': .1}
            with self.assertRaises(ValueError): fresh.check_price('key')
            pricing['pricingModel'] = 'PRICE_PER_DATASET_ITEM'
            with self.assertRaises(ValueError): fresh.check_price('key')

    def test_prompt_has_both_sections_and_existing_format(self):
        text = (ROOT/'cron/twitter_digest.md').read_text()
        for expected in ['关注的人', '全网热门', '📰', '[查看原文 →]', 'digestMaxItems']:
            self.assertIn(expected, text)


if __name__ == '__main__':
    unittest.main()
