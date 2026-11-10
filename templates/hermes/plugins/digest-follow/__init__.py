"""Native Hermes commands; no source patch, LLM or Telegram API calls."""
from functools import partial
import os
from pathlib import Path
import sys


def modules(home):
    scripts = home / 'scripts'
    if not scripts.exists():  # repository tests / plugin doctor
        scripts = Path(__file__).resolve().parents[3] / 'scripts'
    sys.path.insert(0, str(scripts))
    from digest_config import edit_accounts, handle, load_config, locked
    from twitter_profile import lookup_profile
    return edit_accounts, handle, load_config, locked, lookup_profile


class Commands:
    def __init__(self, home, *, lookup=None, env=None):
        self.home = Path(home)
        self.env = os.environ if env is None else env
        self.edit, self.handle, self.load, self.locked, default_lookup = modules(self.home)
        self.lookup = lookup or partial(default_lookup, self.home)
        self.path = self.home / 'twitter/digest.yaml'

    def run(self, command, raw_args, *, source=None, **context):
        platform = getattr(getattr(source, 'platform', None), 'value', None)
        groups = {x.strip() for x in self.env.get('TELEGRAM_GROUP_ALLOWED_CHATS', '').split(',') if x.strip()}
        if (platform != 'telegram' or getattr(source, 'chat_type', None) not in ('group', 'supergroup', 'forum')
                or str(getattr(source, 'chat_id', '')) not in groups):
            return '请在已配置的 Telegram 目标群使用这些命令。'
        try:
            if '\n' in raw_args or '\r' in raw_args or len(raw_args) > 350:
                raise ValueError('usage')
            args = raw_args.split(maxsplit=1)
            if command == 'following':
                if args: raise ValueError('usage')
                accounts = self.load(self.path)['accounts']
                return '\n'.join(k + (' — ' + v if v else '') for k, v in accounts.items()) or '当前没有关注账号。'
            if not args or (command == 'unfollow' and len(args) > 1): raise ValueError('usage')
            username = self.handle(args[0])
            note = args[1].strip() if len(args) > 1 else ''
            if len(note) > 300: raise ValueError('usage')
        except ValueError:
            return '用法：/follow 用户名 [备注]；/unfollow 用户名；/following。用户名为 1～15 位字母、数字或下划线。'
        except Exception:
            return '配置读取失败，请联系实例维护者核对 YAML。'
        try:
            # Serialize lookup + mutation, so duplicate concurrent follows never pay twice.
            with self.locked(self.home/'state/twitter_follow'):
                config = self.load(self.path)
                existing = next((k for k in config['accounts'] if k.lower() == username), None)
                if command == 'unfollow':
                    if not existing: return '当前未关注该账号。'
                    self.edit(self.path, 'remove', username)
                    return '已取消关注 ' + username
                if existing: return '已关注该账号：' + existing
                profile = self.lookup(username, config)
                if (not profile or str(profile.get('userName', '')).lower() != username
                        or not isinstance(profile.get('name'), str)
                        or type(profile.get('followers')) is not int or profile['followers'] < 0):
                    return '暂时无法验证这个账号，未修改关注名单。'
                self.edit(self.path, 'add', username, note)
                name = profile['name'].replace('\n', ' ').replace('\r', ' ')[:100]
                return f"已关注 {name}（{profile['followers']} 粉丝）"
        except Exception:
            # No external response/error text in chat or logs.
            return '账号验证或配置更新失败，未确认关注变更；请用 /following 核对。'


def register(ctx):
    home = Path(os.environ.get('HERMES_HOME', str(Path.home()/'.hermes')))
    commands = Commands(home)
    for name, hint in [('follow', '<username> [note]'), ('unfollow', '<username>'), ('following', '')]:
        ctx.register_command(name, partial(commands.run, name), description='Manage digest follows', args_hint=hint)
