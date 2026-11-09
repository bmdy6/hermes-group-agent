#!/usr/bin/env python3
"""twitterapi.io -> Hermes cron input. Only standard library; --fixture is offline."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


# twitterapi.io 试用账号约每 5 秒只允许 1 次请求；遇到 429 等一轮再试一次。
MIN_INTERVAL = float(os.environ.get("TWITTERAPI_MIN_INTERVAL", "5.5"))
_last_call = [0.0]


def _get(request):
    for attempt in range(2):
        wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code != 429 or attempt:
                raise
            exc.close()


def fetch(account, key):
    cursor = ""
    while True:
        query = urlencode({"userName": account, "cursor": cursor, "includeReplies": "false"})
        request = Request("https://api.twitterapi.io/twitter/user/last_tweets?" + query,
                          headers={"X-API-Key": key})
        page = _get(request)
        yield page
        tweets = unpack(page)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        if not page.get("has_next_page") or not tweets or any(stamp(t) < cutoff for t in tweets):
            break
        following = page.get("next_cursor")
        if not following or following == cursor:
            raise ValueError("invalid pagination cursor")
        cursor = following


def unpack(page):
    if page.get("status") != "success" or str(page.get("code", 0)) != "0":
        raise ValueError("twitterapi.io returned an API error")
    tweets = page.get("data", {}).get("tweets")
    if not isinstance(tweets, list):
        raise ValueError("twitterapi.io response missing data.tweets")
    return tweets


def stamp(tweet):
    return parsedate_to_datetime(tweet["createdAt"]).astimezone(timezone.utc)


def select(pages, seen, now):
    cutoff = now - timedelta(hours=24)
    selected = {}
    for page in pages:
        for tweet in unpack(page):
            created = stamp(tweet)
            tid = str(tweet["id"])
            if not cutoff <= created <= now or tid in seen:
                continue
            if tweet.get("retweeted_tweet") or tweet.get("isRetweet") or tweet["text"].startswith("RT @"):
                continue
            selected[tid] = {"id": tid, "account": tweet["author"]["userName"],
                             "text": tweet["text"], "url": tweet["url"], "createdAt": tweet["createdAt"]}
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="one API response or a list of API response pages")
    parser.add_argument("--accounts", type=Path)
    args = parser.parse_args(argv)
    # Installed script lives in HERMES_HOME/scripts; cron may scrub HERMES_HOME itself.
    home = Path(os.environ["HERMES_HOME"]) if os.environ.get("HERMES_HOME") else Path(__file__).resolve().parents[1]
    try:
        accounts = (args.accounts or home / "twitter/accounts.txt").read_text().splitlines()
        accounts = [a.strip().lstrip("@") for a in accounts if a.strip() and not a.lstrip().startswith("#")]
        if not accounts or any(not re.fullmatch(r"[A-Za-z0-9_]{1,15}", a) for a in accounts):
            raise ValueError("accounts.txt needs valid account handles")
        seen_path = home / "state/twitter_seen.json"
        seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}
        if not isinstance(seen, dict):
            raise ValueError("invalid twitter_seen.json")
        now = datetime.now(timezone.utc)
        if args.fixture:
            data = json.loads(args.fixture.read_text())
            pages = data if isinstance(data, list) else [data]
            selected = select(pages, seen, now)
        else:
            key_file = home / "secrets/twitterapi_io.key"
            key = key_file.read_text().strip() if key_file.exists() else ""
            key = key or os.environ.get("TWITTER_DIGEST_API_KEY", "").strip()
            if not key:
                raise ValueError("missing secrets/twitterapi_io.key (or TWITTER_DIGEST_API_KEY)")
            selected = {}
            succeeded = 0
            for account in dict.fromkeys(accounts):
                try:
                    candidates = select(fetch(account, key), seen, now)
                except Exception as exc:
                    # Account-local failures must not abort other accounts or leak response/key text.
                    print(f"twitter_digest: @{account}: {type(exc).__name__}", file=sys.stderr)
                    continue
                selected.update(candidates)
                succeeded += 1
            if not succeeded:
                return 1
        # Retain seven days of handoff dedup, including on runs with no new tweets.
        seen = {tid: ts for tid, ts in seen.items() if datetime.fromisoformat(ts) >= now - timedelta(days=7)}
        # Handoff dedup only: Hermes owns model execution and Telegram delivery.
        seen.update({tid: now.isoformat() for tid in selected})
        if selected or seen_path.exists():
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(seen, ensure_ascii=False) + "\n")
        if not selected:
            print(json.dumps({"wakeAgent": False}))
            return 0
        print(json.dumps({"tweets": list(selected.values())}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # Never echo HTTP bodies, exception URLs or credentials into cron/model logs.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        print("twitter_digest: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
