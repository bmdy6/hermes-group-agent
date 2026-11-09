"""Offline policy tests; synthetic references only, no credentials/network."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'templates/hermes/plugins/jev-router/__init__.py'
spec = importlib.util.spec_from_file_location('jev_router', path)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

class Contract(unittest.TestCase):
    def test_noul_response(self):
        self.assertEqual(router.parse_scores({'answers': {name: {'noul': .9} for name in router.QUESTIONS}}), [.9, .9, .9])

import asyncio
from datetime import datetime, timedelta, timezone
import json
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock


class Policy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = dict(enabled=True, threshold=.85, cooldown_seconds=600,
                             context_messages=10, timeout_seconds=2)
        self.now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.request = AsyncMock(return_value=[.9, .1, .1])
        self.policy = router.Router(self.settings.get, Path(self.tmp.name),
                                    request=self.request, clock=lambda: self.now,
                                    env={'JEV_API_KEY': 'fixture', 'TELEGRAM_GROUP_ALLOWED_CHATS': 'group'})
        self.store = NS(get_or_create_session=lambda source: NS(session_id='session'),
                        load_transcript=lambda sid: [], append_to_transcript=lambda sid, row: self.rows.append(row))
        self.rows = []
        self.adapter = NS(_message_mentions_bot=lambda raw: raw.mention,
                          _is_reply_to_bot=lambda raw: raw.reply)
        self.gateway = NS(_intake_adapter_for=lambda source: self.adapter,
                          _is_user_authorized_for_source=lambda source: True)

    def event(self, text='Can the assistant help?', **kw):
        raw = NS(text=text, caption=None, mention=kw.pop('mention', False),
                 reply=kw.pop('reply', False), from_user=NS(is_bot=False))
        source = NS(platform=NS(value='telegram'), chat_type='group', chat_id='group',
                    user_id=None, user_name=None, thread_id=None)
        for key, value in kw.items():
            setattr(source, key, value)
        return NS(text=text, source=source, raw_message=raw, message_id='message', internal=False)

    async def dispatch(self, event=None):
        return await self.policy.dispatch(event or self.event(), self.gateway, self.store)

    def records(self):
        return [json.loads(line) for line in (Path(self.tmp.name) / 'state/jev_router.jsonl').read_text().splitlines()]

    async def test_direct_paths_unchanged(self):
        for e in [self.event(mention=True), self.event(reply=True), self.event('/help')]:
            self.assertIsNone(await self.dispatch(e))
        self.request.assert_not_called()
        self.assertFalse(self.rows)

    async def test_threshold_and_diagnostics_only(self):
        self.request.return_value = [.85, 0, 0]
        self.assertIsNone(await self.dispatch())
        self.assertEqual(self.records()[-1]['decision'], 'allow')
        self.assertFalse(self.rows)

    async def test_low_score_observed(self):
        self.request.return_value = [.849, 1, 1]
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.assertEqual(self.rows[0]['content'], self.event().text)
        self.assertTrue(self.rows[0]['observed'])

    async def test_cooldown_concurrent_and_restart(self):
        results = await asyncio.gather(self.dispatch(), self.dispatch())
        self.assertEqual(sum(x is None for x in results), 1)
        self.request.assert_awaited_once()
        fresh = router.Router(self.settings.get, Path(self.tmp.name), request=self.request,
                              clock=lambda: self.now, env=self.policy.env)
        self.assertEqual((await fresh.dispatch(self.event(), self.gateway, self.store))['action'], 'skip')
        self.now += timedelta(seconds=600)
        self.assertIsNone(await self.dispatch())

    async def test_errors_timeout_missing_key_disabled(self):
        for error in [RuntimeError('secret must never escape'), TimeoutError()]:
            self.request.side_effect = error
            self.assertEqual((await self.dispatch())['action'], 'skip')
        self.request.reset_mock()
        self.policy.env = {'TELEGRAM_GROUP_ALLOWED_CHATS': 'group'}
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.request.assert_not_called()
        self.settings['enabled'] = False
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.assertEqual(len(self.rows), 4)
        self.assertNotIn('secret must never escape', json.dumps(self.records()))

    async def test_total_timeout(self):
        self.settings['timeout_seconds'] = .01
        async def slow(*args):
            await asyncio.sleep(.1)
            return [.99] * 3
        self.policy.request = slow
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.assertEqual(self.records()[-1]['decision'], 'error')

    async def test_not_allowlisted_or_not_group_or_other_platform(self):
        for e in [self.event(chat_id='other'), self.event(chat_type='dm'),
                  self.event(platform=NS(value='discord'))]:
            self.assertIsNone(await self.dispatch(e))
        self.request.assert_not_called()
        self.assertFalse(self.rows)

    async def test_auth_before_external_call_or_observation(self):
        self.gateway._is_user_authorized_for_source = lambda source: False
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.request.assert_not_called()
        self.assertFalse(self.rows)

    async def test_retention_privacy_and_context(self):
        old = self.now - timedelta(days=15)
        audit = Path(self.tmp.name) / 'state/jev_router.jsonl'
        audit.parent.mkdir()
        audit.write_text(json.dumps({'time': old.isoformat(), 'decision': 'allow'}) + '\n')
        self.store.load_transcript = lambda sid: [{'role': 'user', 'content': f'past-{i}'} for i in range(20)]
        ident = str(10 ** 8 + 1)
        e = self.event('access id ' + ident + ' github_' + 'pat_' + 'sensitivevalue' + ident)
        await self.dispatch(e)
        records = self.records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertLessEqual(len(record['preview']), 80)
        self.assertNotIn(ident, json.dumps(record))
        self.assertNotIn('sensitivevalue', json.dumps(record))
        self.assertNotEqual(record['group'], 'group')
        self.assertNotEqual(record['message'], 'message')
        context = self.request.call_args.args[0]['recent_messages']
        self.assertEqual(len(context), 10)
        self.assertEqual(context[0]['text'], router.safe_text('past-10'))

    async def test_bad_response_and_settings_fail_closed(self):
        self.request.return_value = [float('nan'), .9, .9]
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.settings['threshold'] = 0
        self.assertEqual((await self.dispatch())['action'], 'skip')

    def test_strict_noul_schema(self):
        for v in [True, '0.9', float('nan'), float('inf'), -.1, 1.1]:
            with self.assertRaises(ValueError):
                router.parse_scores({'answers': {name: {'noul': v} for name in router.QUESTIONS}})

    async def test_missing_host_handle_never_falls_through(self):
        self.gateway = None
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.request.assert_not_called()

    async def test_audit_failure_does_not_allow_high_score(self):
        self.policy._record = lambda *args: (_ for _ in ()).throw(OSError('fixture'))
        self.assertEqual((await self.dispatch())['action'], 'skip')
        self.assertTrue(self.rows[0]['observed'])
        self.assertFalse(self.policy.last_allowed)

    async def test_retention_boundary_and_empty_key_privacy(self):
        audit = Path(self.tmp.name) / 'state/jev_router.jsonl'
        audit.parent.mkdir()
        audit.write_text(''.join(json.dumps({'time': (self.now - timedelta(days=age)).isoformat(),
                                            'decision': 'low_score'}) + '\n' for age in [14, 15]))
        self.policy.env.pop('JEV_API_KEY')
        await self.dispatch()
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(self.records()[-1]['scores'], None)
        self.assertEqual(self.records()[-1]['decision'], 'missing_key')


class Transport(unittest.IsolatedAsyncioTestCase):
    async def test_three_noul_questions_in_one_request(self):
        from unittest.mock import patch
        import sys
        payload = {'answers': {name: {'noul': .9} for name in router.QUESTIONS}}
        response = NS(raise_for_status=lambda: None, json=lambda: payload)
        client = NS(post=AsyncMock(return_value=response))
        class Client:
            async def __aenter__(self):
                return client
            async def __aexit__(self, *args):
                return False
        fake_httpx = NS(AsyncClient=lambda **kwargs: Client())
        with patch.dict(sys.modules, {'httpx': fake_httpx}):
            scores = await router.evaluate({'current_message': 'hello'}, 'fixture', 'jev-latest', 2)
        self.assertEqual(scores, [.9] * 3)
        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        self.assertEqual(args, (router.ENDPOINT,))
        self.assertEqual(set(kwargs['json']['questions']), set(router.QUESTIONS))
        self.assertTrue(all(q['type'] == 'noul' for q in kwargs['json']['questions'].values()))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer fixture')
