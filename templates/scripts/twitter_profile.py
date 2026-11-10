"""Exact profile lookup using Apify, with a reserved daily budget and hard run cap."""
from datetime import datetime, timezone, timedelta
import json
import os
from urllib.parse import urlencode

from digest_config import handle, locked
from twitter_digest import api, write_json, TERMINAL

ACTOR = 'apidojo~twitter-user-scraper'


def check_profile_price(key):
    actor = api('/acts/' + ACTOR, key)['data']
    now = datetime.now(timezone.utc)
    prices = [p for p in actor['pricingInfos'] if datetime.fromisoformat(p['startedAt'].replace('Z', '+00:00')) <= now]
    price = max(prices, key=lambda p: p['startedAt'])
    events = price.get('pricingPerEvent', {}).get('actorChargeEvents', {})
    # This exact operation triggers one URL query and one dataset item, no relationship queries.
    if price['pricingModel'] != 'PAY_PER_EVENT' or set(events) != {
            'dataset-item', 'single-url-event', 'about-event', 'retweeters-event',
            'following-event', 'follower-event', 'keyword-search-event'}:
        raise ValueError('unsupported profile pricing')
    for name, ceiling in [('single-url-event', .004), ('dataset-item', .0004)]:
        rate = events[name].get('eventPriceUsd')
        if not isinstance(rate, (int, float)) or not 0 < rate <= ceiling:
            raise ValueError('profile price exceeds ceiling')


def lookup_profile(home, username, config):
    username = handle(username)
    key_file = home/'secrets/apify.key'
    key = key_file.read_text().strip() if key_file.exists() else ''
    key = key or os.environ.get('APIFY_TOKEN', '').strip()
    if not key: raise ValueError('missing Apify credential')
    path = home/'state/twitter_lookup_budget.json'
    date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    cap = float(config['lookup']['max_charge_usd'])
    with locked(path):
        budget = json.loads(path.read_text()) if path.exists() else {}
        if budget.get('date') != date: budget = {'date': date, 'calls': 0, 'reservedUsd': 0}
        if type(budget['calls']) is not int or budget['calls'] < 0:
            raise ValueError('invalid lookup budget')
        if budget['calls'] >= config['lookup']['daily_max_calls']:
            raise ValueError('lookup daily cap reached')
        check_profile_price(key)
        budget['calls'] += 1
        budget['reservedUsd'] += cap
        write_json(path, budget)  # uncertain launch/failure is never refunded or retried
        params = urlencode({'waitForFinish': 60, 'timeout': 120, 'maxItems': 1, 'maxTotalChargeUsd': cap})
        run = api('/acts/' + ACTOR + '/runs?' + params, key, {
            'startUrls': ['https://x.com/' + username], 'maxItems': 1,
            'getFollowers': False, 'getFollowing': False, 'getRetweeters': False,
            'getAbout': False, 'includeUnavailableUsers': False})['data']
        for _ in range(3):
            if run['status'] in TERMINAL: break
            run = api('/actor-runs/' + run['id'] + '?waitForFinish=60', key)['data']
        if run['status'] not in TERMINAL:
            api('/actor-runs/' + run['id'] + '/abort', key, {})
            raise ValueError('profile lookup timed out')
        if run['status'] != 'SUCCEEDED': raise ValueError('profile lookup failed')
        items = api('/datasets/' + run['defaultDatasetId'] + '/items?clean=true&limit=1', key)
        if not isinstance(items, list): raise ValueError('invalid profile response')
        for item in items:
            if str(item.get('userName', '')).lower() == username and not item.get('unavailable'):
                return {k: item.get(k) for k in ('userName', 'name', 'followers')}
        return None
