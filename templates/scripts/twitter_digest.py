#!/usr/bin/env python3
"""Apify -> Hermes cron candidates. Round-trip YAML config; --fixture is offline."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest_config import load_config


ACTOR = 'kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest'
API = 'https://api.apify.com/v2'
# Free-plan price verified from Actor pricingInfos. Reject higher prices before launch.
PRICE = 0.00025
TERMINAL = {'SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'}


def api(path, key, payload=None):
    request = Request(API + path, data=None if payload is None else json.dumps(payload).encode(),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urlopen(request, timeout=70) as response:
        return json.load(response)


def check_price(key):
    actor = api('/acts/' + ACTOR, key)['data']
    now = datetime.now(timezone.utc)
    prices = [p for p in actor['pricingInfos'] if datetime.fromisoformat(p['startedAt'].replace('Z', '+00:00')) <= now]
    price = max(prices, key=lambda p: p['startedAt'])
    events = price.get('pricingPerEvent', {}).get('actorChargeEvents', {})
    # Only this known per-result event, with no start/platform charge, is supported.
    if price['pricingModel'] != 'PAY_PER_EVENT' or set(events) != {'apify-default-dataset-item'}:
        raise ValueError('unsupported Actor pricing')
    event = events['apify-default-dataset-item']
    rate = event.get('eventPriceUsd')
    if rate is None:
        rate = event['eventTieredPricingUsd']['FREE']['tieredEventPriceUsd']
    if not 0 < rate <= PRICE:
        raise ValueError('Actor price exceeds configured ceiling')


def fetch(key, job, limit):
    # Input maxItems can over-return. Dollar cap applies to current PAY_PER_EVENT pricing.
    params = urlencode({'waitForFinish': 60, 'timeout': 180, 'maxItems': limit,
                        'maxTotalChargeUsd': f'{limit * PRICE:.8f}'})
    run = api('/acts/' + ACTOR + '/runs?' + params, key,
              {'twitterContent': job['query'], 'queryType': job['sort'], 'maxItems': limit})['data']
    # Server-side long polling is bounded; never retry POST (could create a second paid run).
    for _ in range(4):
        if run['status'] in TERMINAL:
            break
        run = api('/actor-runs/' + run['id'] + '?waitForFinish=60', key)['data']
    if run['status'] not in TERMINAL:
        api('/actor-runs/' + run['id'] + '/abort', key, {})
        raise ValueError('Actor polling timed out')
    if run['status'] != 'SUCCEEDED':
        raise ValueError('Actor run failed')
    items = api('/datasets/' + run['defaultDatasetId'] + '/items?' +
                urlencode({'clean': 'true', 'limit': limit}), key)
    if not isinstance(items, list) or run.get('usageTotalUsd') is None:
        raise ValueError('invalid Actor output or missing usage')
    return items, {k: run.get(k) for k in ('id', 'status', 'usageTotalUsd', 'usage', 'usageUsd',
                                          'chargedEventCounts', 'eventUsage')}


def jobs(accounts, now, total, config):
    window = f'since_time:{int((now - timedelta(hours=24)).timestamp())} until_time:{int(now.timestamp())} -filter:retweets -filter:replies'
    hot = [{'source': 'hot', 'lang': route['lang'], 'min_likes': route['min_likes'], 'limit': route['limit'],
            'query': f"{route['keywords']} min_faves:{route['min_likes']} lang:{route['lang']} {window}", 'sort': 'Top'}
           for route in config['searches']]
    size = config['accounts_per_query']
    chunks = [accounts[i:i+size] for i in range(0, len(accounts), size)]
    hot_total = sum(j['limit'] for j in hot)
    follow_total = max(0, total - hot_total) if total > hot_total else total // (len(hot) + 1)
    follow = []
    for i, chunk in enumerate(chunks):
        quota = follow_total // len(chunks) + (i < follow_total % len(chunks))
        if quota:
            query = '(' + ' OR '.join('from:' + a for a in chunk) + ') ' + window
            follow.append({'source': 'follow', 'limit': quota, 'query': query, 'sort': 'Latest'})
    return follow + hot


def stamp(tweet):
    value = tweet['createdAt']
    try:
        result = parsedate_to_datetime(value)
    except ValueError:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('tweet timestamp needs timezone')
    return result.astimezone(timezone.utc)


def select(batches, seen, now, accounts, total):
    selected = {}
    # Follow wins across sources, independent of fixture ordering.
    for batch in sorted(batches, key=lambda b: b['source'] != 'follow'):
        if batch['source'] not in ('follow', 'hot') or not isinstance(batch['items'], list):
            raise ValueError('invalid fixture batch')
        tweets = batch['items']
        if batch['source'] == 'hot':
            tweets = sorted(tweets, key=lambda t: int(t.get('likeCount', 0)), reverse=True)
        accepted = 0
        for tweet in tweets:
            # Actors sometimes emit a no-results placeholder instead of a tweet.
            if tweet.get('noResults') is True or tweet.get('type') == 'mock_tweet':
                continue
            tid = str(tweet['id'])
            if not now - timedelta(hours=24) <= stamp(tweet) <= now or tid in seen or tid in selected:
                continue
            if tweet.get('retweeted_tweet') or tweet.get('isRetweet') or tweet.get('isReply') or tweet['text'].startswith('RT @'):
                continue
            author = tweet['author']
            if batch['source'] == 'follow' and author['userName'].lower() not in {a.lower() for a in accounts}:
                continue
            if batch['source'] == 'hot' and (int(tweet.get('likeCount', 0)) < batch.get('min_likes', 0) or
                    (batch.get('lang') and tweet.get('lang') != batch['lang'])):
                continue
            if accepted >= batch.get('limit', total) or len(selected) >= total:
                break
            selected[tid] = {'id': tid, 'account': author['userName'], 'displayName': author.get('name') or author['userName'],
                             'text': tweet['text'], 'url': tweet['url'], 'createdAt': tweet['createdAt'],
                             'source': batch['source'], 'lang': tweet.get('lang'), 'likeCount': int(tweet.get('likeCount', 0))}
            accepted += 1
    return selected


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False) + '\n')
    temporary.replace(path)


def collect(home, key, accounts, now, total, calls, config):
    path = home / 'state/twitter_budget.json'
    date = now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    budget = json.loads(path.read_text()) if path.exists() else {}
    if budget.get('date') != date:
        budget = {'date': date, 'calls': 0, 'items': 0}
    for name in ('calls', 'items'):
        if type(budget[name]) is not int or budget[name] < 0:
            raise ValueError('invalid daily budget state')
    batches, runs = [], []
    if budget['calls'] >= calls or budget['items'] >= total:
        return batches, runs
    check_price(key)
    succeeded = 0
    attempted = 0
    for job in jobs(accounts, now, total, config):
        limit = min(job['limit'], total - budget['items'])
        if budget['calls'] >= calls or limit <= 0:
            break
        # Reserve before POST, never refund: timeouts/crashes may still have launched a paid run.
        budget['calls'] += 1
        budget['items'] += limit
        write_json(path, budget)
        attempted += 1
        try:
            items, run = fetch(key, job, limit)
            batch = {**job, 'limit': limit, 'items': items[:limit]}
            # Validate this query independently before accepting its candidates.
            select([batch], {}, now, accounts, total)
        except Exception as exc:
            print(f"twitter_digest: {job['source']}: {type(exc).__name__}", file=sys.stderr)
            continue
        batches.append(batch)
        runs.append({**run, 'source': job['source'], 'lang': job.get('lang'), 'returned': len(items)})
        succeeded += 1
    if attempted and not succeeded:
        raise ValueError('all searches failed')
    return batches, runs


def execute(args, home):
    config = load_config(args.config or home / 'twitter/digest.yaml')
    accounts = list(config['accounts'])
    total = config['daily']['max_items']
    calls = config['daily']['max_calls']
    seen_path = home / 'state/twitter_seen.json'
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}
    if not isinstance(seen, dict):
        raise ValueError('invalid twitter_seen.json')
    now = datetime.now(timezone.utc)
    # Validate/prune before any paid side effects, even for dry-run.
    seen = {tid: ts for tid, ts in seen.items() if datetime.fromisoformat(ts) >= now - timedelta(days=7)}
    if args.fixture:
        batches = json.loads(args.fixture.read_text())
        if not isinstance(batches, list):
            raise ValueError('fixture must be a list of source/items batches')
        runs = []
    else:
        key_file = home / 'secrets/apify.key'
        key = key_file.read_text().strip() if key_file.exists() else ''
        key = key or os.environ.get('APIFY_TOKEN', '').strip()
        if not key:
            # This fixed message contains no user-supplied values.
            print('twitter_digest: missing secrets/apify.key (or APIFY_TOKEN)', file=sys.stderr)
            return 1
        batches, runs = collect(home, key, accounts, now, total, calls, config)
    selected = select(batches, seen, now, accounts, total)
    if args.dry_run:
        print(json.dumps({'dryRun': True, 'returned': sum(len(b['items']) for b in batches),
                          'selected': len(selected), 'sources': {s: sum(t['source'] == s for t in selected.values()) for s in ('follow', 'hot')},
                          'runs': runs, 'usageTotalUsd': sum(r['usageTotalUsd'] for r in runs),
                          'usageNote': 'Snapshot at completion; re-read actor-runs/<id> after billing settles'}, ensure_ascii=False))
        return 0
    # Handoff dedup only: Hermes owns model execution and Telegram delivery.
    seen.update({tid: now.isoformat() for tid in selected})
    if selected or seen_path.exists():
        write_json(seen_path, seen)
    print(json.dumps({'digestMaxItems': config['digest']['max_items'], 'tweets': list(selected.values())} if selected else {'wakeAgent': False}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, help='offline list of {source, items, lang?, min_likes?, limit?} batches')
    parser.add_argument('--config', type=Path, help='active digest YAML (default: HERMES_HOME/twitter/digest.yaml)')
    parser.add_argument('--dry-run', action='store_true', help='only fetch; report counts/usage; do not update seen')
    args = parser.parse_args(argv)
    home = Path(os.environ['HERMES_HOME']) if os.environ.get('HERMES_HOME') else Path(__file__).resolve().parents[1]
    try:
        if args.fixture:
            return execute(args, home)
        (home / 'state').mkdir(parents=True, exist_ok=True)
        with (home / 'state/twitter_digest.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return execute(args, home)
    except Exception as exc:
        # Never echo external error text, HTTP bodies, request URLs or credentials.
        print('twitter_digest: ' + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
