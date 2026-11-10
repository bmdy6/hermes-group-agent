"""Round-trip digest configuration. Writers share a stable lock across atomic replaces."""
from contextlib import contextmanager
import fcntl
import io
import os
from pathlib import Path
import re
import tempfile
from collections.abc import Mapping

from ruamel.yaml import YAML


def yaml():
    value = YAML()
    value.preserve_quotes = True
    value.allow_duplicate_keys = False
    return value


def handle(value):
    value = value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', value):
        raise ValueError('invalid username')
    return value.lower()


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError('invalid configuration number')


def validate(config):
    if not isinstance(config, Mapping) or not isinstance(config.get('accounts'), Mapping):
        raise ValueError('accounts must be a mapping')
    keys = []
    for name, note in config['accounts'].items():
        if not isinstance(name, str) or handle(name) != name.lower() or name.startswith('@'):
            raise ValueError('invalid account key')
        if not isinstance(note, str) or len(note) > 300 or '\n' in note or '\r' in note:
            raise ValueError('invalid account note')
        keys.append(handle(name))
    if len(keys) != len(set(keys)) or len(keys) > 500:
        raise ValueError('duplicate or too many accounts')
    try:
        number(config['daily']['max_calls'], 1, 100)
        number(config['daily']['max_items'], 1, (10 ** 4))
        number(config['digest']['max_items'], 1, 100)
        number(config['accounts_per_query'], 1, 20)
        number(config['lookup']['daily_max_calls'], 1, 100)
        cap = config['lookup']['max_charge_usd']
        if isinstance(cap, bool) or not isinstance(cap, (float, int)) or not .0044 <= cap <= .01:
            raise ValueError('invalid lookup charge cap')
        if not isinstance(config['searches'], list) or len(config['searches']) > 10:
            raise ValueError('invalid search routes')
        for route in config['searches']:
            if not isinstance(route['keywords'], str) or not route['keywords'].strip() or len(route['keywords']) > 500:
                raise ValueError('invalid search keywords')
            if not re.fullmatch('[a-z]{2}', route['lang']):
                raise ValueError('invalid search language')
            number(route['min_likes'], 0, (10 ** 6))
            number(route['limit'], 1, 20)
    except (KeyError, TypeError) as exc:
        raise ValueError('incomplete digest configuration') from exc
    return config


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save(path, config):
    validate(config)
    stream = io.StringIO()
    yaml().dump(config, stream)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(stream.getvalue())
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def load_config(path):
    path = Path(path)
    # Migration template is installed alongside the active file; never read for normal runs.
    if not path.exists():
        initialize(path, path.with_name('digest.defaults.yaml'))
    return validate(yaml().load(path.read_text()))


def initialize(path, template):
    path = Path(path)
    with locked(path):
        if path.exists():
            return validate(yaml().load(path.read_text()))
        config = validate(yaml().load(Path(template).read_text()))
        legacy = path.with_name('accounts.txt')
        if legacy.exists():
            config['accounts'].clear()
            for line in legacy.read_text().splitlines():
                if line.strip() and not line.lstrip().startswith('#'):
                    config['accounts'].setdefault(handle(line.strip()), '')
        save(path, config)
        # Only delete after the complete config is committed and readable.
        validate(yaml().load(path.read_text()))
        if legacy.exists(): legacy.unlink()
        return config


def edit_accounts(path, action, username, note=''):
    username = handle(username)
    load_config(path)  # initialize before acquiring the writer lock
    with locked(path):
        config = load_config(path)
        accounts = config['accounts']
        key = next((k for k in accounts if k.lower() == username), None)
        if action == 'add':
            if key is not None: raise ValueError('already followed')
            accounts[username] = note
        elif action == 'remove':
            if key is None: raise ValueError('not followed')
            del accounts[key]
        else:
            raise ValueError('invalid action')
        save(Path(path), config)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('template', type=Path)
    args = parser.parse_args()
    initialize(args.destination, args.template)
