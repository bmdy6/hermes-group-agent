import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'templates/scripts'))
import twitter_profile as profile


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config = {'lookup': {'max_charge_usd': .005, 'daily_max_calls': 1}}

    def test_exact_lookup_disabled_relationships_budget_and_no_retry(self):
        calls = []
        def api(path, key, payload=None):
            calls.append((path, key, payload))
            if '/runs?' in path:
                return {'data': {'status': 'SUCCEEDED', 'id': 'fixture-run', 'defaultDatasetId': 'fixture-dataset'}}
            return [{'userName': 'alice', 'name': 'Alice', 'followers': 42}]
        with patch.dict(os.environ, {'APIFY_TOKEN': 'fixture-key'}), patch.object(profile, 'check_profile_price'), patch.object(profile, 'api', side_effect=api):
            self.assertEqual(profile.lookup_profile(self.home, 'alice', self.config)['followers'], 42)
            self.assertIn('maxTotalChargeUsd=0.005', calls[0][0])
            self.assertEqual(calls[0][2]['startUrls'], ['https://x.com/alice'])
            for key in ['getFollowers', 'getFollowing', 'getRetweeters', 'getAbout']: self.assertFalse(calls[0][2][key])
            with self.assertRaises(ValueError): profile.lookup_profile(self.home, 'bob', self.config)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads((self.home/'state/twitter_lookup_budget.json').read_text())['calls'], 1)

    def test_timeout_reservation_not_refunded_or_retried(self):
        with patch.dict(os.environ, {'APIFY_TOKEN': 'fixture-key'}), patch.object(profile, 'check_profile_price'), patch.object(profile, 'api', side_effect=TimeoutError) as api:
            with self.assertRaises(TimeoutError): profile.lookup_profile(self.home, 'alice', self.config)
            with self.assertRaises(ValueError): profile.lookup_profile(self.home, 'alice', self.config)
            self.assertEqual(api.call_count, 1)

    def test_price_increase_or_new_events_fail_closed(self):
        events = {k: {'eventPriceUsd': v} for k,v in [('dataset-item', .0004), ('single-url-event', .004), ('about-event', .0002), ('retweeters-event', .016), ('following-event', .016), ('follower-event', .016), ('keyword-search-event', .008)]}
        price = {'startedAt': '2020-01-01T00:00:00Z', 'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {'actorChargeEvents': events}}
        with patch.object(profile, 'api', return_value={'data': {'pricingInfos': [price]}}):
            profile.check_profile_price('key')
            events['single-url-event']['eventPriceUsd'] = .01
            with self.assertRaises(ValueError): profile.check_profile_price('key')
            events['single-url-event']['eventPriceUsd'] = .004
            events['start'] = {'eventPriceUsd': .1}
            with self.assertRaises(ValueError): profile.check_profile_price('key')
