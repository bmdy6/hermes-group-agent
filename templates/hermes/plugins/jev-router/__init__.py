"""Opt-in ambient Telegram routing for unmodified Hermes v0.23.0.

The adapter's mention gate runs BEFORE this hook. Keep it on by default;
only disable it together with enabling this plugin (see README).
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

QUESTIONS = {
    'addressed': 'The current message is directed at the assistant, rather than another person in the group.',
    'request': 'The current message is a question or a request.',
    'helpful': 'The assistant can help using the recent group conversation or its available read-only tools.',
}
ENDPOINT = 'https://api.typesafe.ai/v1/systemone'


def safe_text(value):
    """No credentials or numeric identifiers leave this boundary, even in previews."""
    text = str(value or '')
    # Known secrets also cover unusual formats; generic tokens cover accidental pastes.
    for name, secret in os.environ.items():
        if any(part in name.upper() for part in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET')) and len(secret) >= 6:
            text = text.replace(secret, '[secret]')
    text = re.sub(r'(?i)\b(?:bearer\s+|(?:api[_ -]?key|token|password|secret)\s*[:=]\s*)\S+', '[secret]', text)
    text = re.sub(r'[A-Za-z0-9_-]{24,}', '[secret]', text)
    return re.sub(r'(?<!\w)-?\d+(?!\w)', '[number]', text)


def ref(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:24]


def parse_scores(body):
    try:
        values = [body['answers'][name]['noul'] for name in QUESTIONS]
    except (KeyError, TypeError) as exc:
        raise ValueError('Invalid Jev response') from exc
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        raise ValueError('Invalid Jev scores')
    return values


async def evaluate(state, key, model, timeout):
    # httpx is already a Hermes dependency. No extra SDK and no retry.
    import httpx
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(ENDPOINT, headers={'Authorization': f'Bearer {key}'}, json={
            'model': model, 'state': state,
            'questions': {name: {'type': 'noul', 'instructions': question} for name, question in QUESTIONS.items()},
        })
        response.raise_for_status()
        return parse_scores(response.json())


class Router:
    def __init__(self, get_config, home, *, request=evaluate, clock=None, env=None):
        self.get = get_config
        self.home = Path(home)
        self.request = request
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.env = os.environ if env is None else env
        self.locks = {}
        self.buffers = {}
        self.last_allowed = {}
        self.audit = self.home / 'state/jev_router.jsonl'
        self._restore_cooldowns()

    def _restore_cooldowns(self):
        if not self.audit.exists():
            return
        # If the audit cannot be read, the caller must not let a registration error
        # silently remove the policy gate: dispatch will fail closed instead.
        try:
            for line in self.audit.read_text().splitlines():
                row = json.loads(line)
                if row.get('decision') == 'allow':
                    when = datetime.fromisoformat(row['time'])
                    if when.tzinfo is None:
                        raise ValueError('Naive audit time')
                    self.last_allowed[row['group']] = max(when, self.last_allowed.get(row['group'], when))
        except Exception:
            self.audit_broken = True

    def _settings(self):
        threshold = self.get('threshold', .85)
        cooldown = self.get('cooldown_seconds', 600)
        count = self.get('context_messages', 10)
        timeout = self.get('timeout_seconds', 2)
        if (type(threshold) not in (int, float) or not .85 <= threshold <= 1
                or type(cooldown) not in (int, float) or not math.isfinite(cooldown) or cooldown < 600
                or type(count) is not int or not 1 <= count <= 50
                or type(timeout) not in (int, float) or not 0 < timeout <= 2):
            raise ValueError('Unsafe router settings')
        return threshold, cooldown, count, timeout

    def _record(self, event, scores, decision):
        now = self.clock()
        cutoff = now - timedelta(days=14)
        self.audit.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        retained = []
        if self.audit.exists():
            for line in self.audit.read_text().splitlines():
                try:
                    row = json.loads(line)
                    if datetime.fromisoformat(row['time']) >= cutoff:
                        retained.append(json.dumps(row, ensure_ascii=False))
                except (ValueError, KeyError, TypeError):
                    continue
        row = {'time': now.isoformat(), 'group': ref(event.source.chat_id),
               'message': ref(f'{event.source.chat_id}:{event.message_id}'),
               'preview': safe_text(self._text(event))[:80], 'scores': scores, 'decision': decision}
        # Atomic compaction plus append, serialized on the event loop (no await here).
        tmp = self.audit.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join(retained + [json.dumps(row, ensure_ascii=False)]) + '\n')
        os.replace(tmp, self.audit)

    @staticmethod
    def _text(event):
        raw = event.raw_message
        return getattr(raw, 'text', None) or getattr(raw, 'caption', None) or event.text or ''

    @staticmethod
    def _observe(event, store):
        # The adapter already applies shared-source attribution before dispatch.
        # Match native observe: same session, user role, timestamp, observed marker,
        # and platform message reference; no model call and no Telegram send.
        entry = store.get_or_create_session(event.source)
        store.append_to_transcript(entry.session_id, {
            'role': 'user', 'content': event.text, 'observed': True,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'message_id': str(event.message_id or ''),
        })

    def _context(self, event, store, count):
        # Keep messages individually rather than merging adjacent observed user rows.
        # Seed from native transcript on first use; live events include direct addresses.
        key = (str(event.source.chat_id), str(event.source.thread_id))
        if key not in self.buffers:
            entry = store.get_or_create_session(event.source)
            history = store.load_transcript(entry.session_id)
            self.buffers[key] = deque(({'role': row['role'], 'text': safe_text(row.get('content', ''))[:4000]}
                                       for row in history if row.get('role') in {'user', 'assistant'}), maxlen=50)
        buf = self.buffers[key]
        context = list(buf)[-count:]
        buf.append({'role': 'user', 'text': safe_text(self._text(event))[:4000]})
        return context

    async def dispatch(self, event, gateway, session_store, **kwargs):
        source = event.source
        if (event.internal or source is None or getattr(source.platform, 'value', '') != 'telegram'
                or source.chat_type not in {'group', 'supergroup'}):
            return None
        allowed = {x.strip() for x in self.env.get('TELEGRAM_GROUP_ALLOWED_CHATS', '').split(',') if x.strip()}
        if str(source.chat_id) not in allowed:
            return None
        # Native pre_gateway_dispatch is before gateway authorization; do not send
        # denied messages to Jev or record them in a shared conversation.
        try:
            authorized = gateway._is_user_authorized_for_source(source)
        except Exception:
            authorized = False
        if not authorized:
            return {'action': 'skip', 'reason': 'jev-router'}
        scores = None
        decision = 'error'
        try:
            adapter = gateway._intake_adapter_for(source)
            raw = event.raw_message
            direct = (self._text(event).lstrip().startswith('/')
                      or (adapter and raw and (adapter._message_mentions_bot(raw) or adapter._is_reply_to_bot(raw))))
            # Direct messages never call Jev and never consume the proactive cooldown.
            if direct:
                try:
                    self._context(event, session_store, 10)
                except Exception:
                    pass
                return None
            lock = self.locks.setdefault(str(source.chat_id), asyncio.Lock())
            async with lock:
                try:
                    threshold, cooldown, count, timeout = self._settings()
                    context = self._context(event, session_store, count)
                    if getattr(self, 'audit_broken', False):
                        raise ValueError('Audit unavailable')
                    if self.get('enabled', False) is not True:
                        decision = 'disabled'
                    elif getattr(getattr(raw, 'from_user', None), 'is_bot', False):
                        decision = 'bot'
                    elif not self.env.get('JEV_API_KEY'):
                        decision = 'missing_key'
                    elif (self.clock() - self.last_allowed.get(ref(source.chat_id),
                          datetime.min.replace(tzinfo=timezone.utc))).total_seconds() < cooldown:
                        decision = 'cooldown'
                    else:
                        state = {'recent_messages': context, 'current_message': safe_text(self._text(event))[:4000],
                                 'assistant': self.get('assistant_name', 'the group assistant'),
                                 'tools': ['web search', 'group conversation', 'enabled read-only MCP']}
                        async with asyncio.timeout(timeout):
                            scores = await self.request(state, self.env['JEV_API_KEY'], self.get('jev_model', 'jev-latest'), timeout)
                        scores = parse_scores({'answers': {name: {'noul': v} for name, v in zip(QUESTIONS, scores)}})
                        decision = 'allow' if scores[0] >= threshold else 'low_score'
                    self._record(event, scores, decision)
                    if decision == 'allow':
                        self.last_allowed[ref(source.chat_id)] = self.clock()
                        # The adapter marks unaddressed chatter False; the router's
                        # admitted turn now explicitly expects a response.
                        event.reply_expected = True
                        return None
                except Exception:
                    # Never return exception strings or response bodies to the host logger.
                    try:
                        self._record(event, None, 'error')
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self._observe(event, session_store)
        except Exception:
            pass  # Even a broken local store must never turn a rejection into a reply.
        # Hermes logs skip reasons, so use a constant without chat IDs or message text.
        return {'action': 'skip', 'reason': 'jev-router'}


def register(ctx):
    from hermes_constants import get_hermes_home
    router = Router(ctx.get_config, get_hermes_home())
    ctx.register_hook('pre_gateway_dispatch', router.dispatch)
