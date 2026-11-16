from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import main
from data import read_gold
from errors import JobError
from helpers import DAY, ID, arrow_table, row
from http_fakes import acknowledgement, profile
from model import train
from state import Store
from test_settings import config


class Clock:
    def __init__(self, value=None):
        self.value = value or datetime(2026, 11, 12, 13, tzinfo=timezone.utc)
        self.waits = []
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += timedelta(seconds=seconds)


class Client:
    def __init__(self, ids=(ID,)):
        self.ads = {ident: {'ad_group_id': ident, 'profile': profile(ident)} for ident in ids}
        self.writes = []
        self.reads = 0
        self.request_ids = []
        self.on_write = None
        self.on_list = None
    def list_ads(self):
        self.reads += 1
        self.request_ids.append(ID)
        if self.on_list:
            self.on_list(self)
        return deepcopy(self.ads)
    def write_weights(self, items):
        self.writes.append(deepcopy(items))
        self.request_ids.append(ID)
        if self.on_write:
            return self.on_write(self, items)
        return acknowledgement(items)['items']


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = config(state_dir=self.root / 'state', train_epochs=2)
        self.clock = Clock()
        self.client = Client()

    def run_job(self, rows=None, *, reader=None, settings=None, client=None, store=None, dry_run=False, clock=None):
        clock = clock or self.clock
        output = io.StringIO()
        with redirect_stdout(output):
            code = main.execute(settings or self.settings, client=client or self.client, store=store,
                                reader=reader or (lambda *args: (rows if rows is not None else [row()], 7)),
                                dry_run=dry_run, clock=clock.now, sleep=clock.sleep)
        logs = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertNotIn('NEVER_STORE_THIS_KEY', output.getvalue())
        self.assertTrue(all(x['source'] == 'mlpregressor' for x in logs))
        return code, logs

    def state(self, settings=None):
        return Store((settings or self.settings).state_dir, (settings or self.settings).identity).load()

    def test_actual_delta_to_actual_mlp_and_duplicate_execution(self):
        from deltalake import write_deltalake
        uri = self.root / 'gold'
        write_deltalake(str(uri), arrow_table([row(), row(kst_date=DAY - timedelta(days=1))]))
        settings = replace(self.settings, gold_uri=str(uri))
        code, logs = self.run_job(reader=read_gold, settings=settings)
        self.assertEqual(code, 0)
        self.assertEqual(logs[-1]['planned_rows'], 1)
        self.assertEqual(logs[-1]['trained_rows'], 1)
        state = self.state(settings)
        self.assertEqual(state['completed_date'], DAY.isoformat())
        self.assertIsNone(state['pending'])
        before = deepcopy(self.client.writes)
        with patch('main.train') as learning:
            code, logs = self.run_job(reader=lambda *args: self.fail('Gold reread'), settings=settings)
        learning.assert_not_called()
        self.assertEqual((code, logs[-1]['status']), (0, 'already_completed'))
        self.assertEqual(self.client.writes, before)
        self.assertEqual(len(list((settings.state_dir / 'models').glob('*.joblib'))), 1)

    def test_holds_keep_new_and_existing_models(self):
        for seeded in (False, True):
            for name, rows, reads in [('empty', [], 3), ('unconfirmed', [row(confirmed=False)], 3),
                                      ('null', [row(mlo_ctr=None)], 1)]:
                with self.subTest(seeded=seeded, reason=name):
                    settings = replace(self.settings, state_dir=self.root / f'{seeded}-{name}')
                    client, clock = Client(), Clock()
                    if seeded:
                        self.assertEqual(self.run_job(settings=settings, client=client, clock=clock)[0], 0)
                        clock.value += timedelta(days=1)
                        rows = [{**x, 'kst_date': DAY + timedelta(days=1)} for x in rows]
                    before = self.state(settings)
                    client.writes.clear()
                    calls = []
                    def reader(*args):
                        calls.append(args[1])
                        return rows, 8
                    with patch('main.train') as learning:
                        code, logs = self.run_job(settings=settings, client=client, clock=clock, reader=reader)
                    learning.assert_not_called()
                    after = self.state(settings)
                    self.assertEqual(code, 75)
                    self.assertEqual(len(calls), reads)
                    self.assertEqual(client.writes, [[]])
                    self.assertEqual(after['model_version'], before['model_version'])
                    self.assertEqual(after['trained_date'], before['trained_date'])
                    self.assertEqual(logs[-1]['trained_rows'], 0)

    def test_unconfirmed_becomes_confirmed_same_day(self):
        snapshots = iter([([row(confirmed=False)], 1), ([row()], 2)])
        with patch('main.train', wraps=train) as learning:
            code, logs = self.run_job(reader=lambda *args: next(snapshots))
        self.assertEqual(code, 0)
        self.assertEqual(learning.call_count, 1)
        self.assertEqual(self.clock.waits, [600])
        self.assertEqual([len(x) for x in self.client.writes], [0, 1])
        self.assertEqual(logs[-1]['gold_version'], 2)
        self.assertEqual(self.state()['model_version'], 'v000001')

    def test_unconfirmed_empty_failure_retries_empty_before_gold_reread(self):
        events = []
        snapshots = iter([([row(confirmed=False)], 1), ([row()], 2)])
        def reader(*args):
            events.append('Gold')
            return next(snapshots)
        def first_empty_fails(client, items):
            events.append(f'POST{len(items)}')
            if len(client.writes) == 1:
                raise JobError('api_unavailable', retryable=True)
            return acknowledgement(items)['items']
        self.client.on_write = first_empty_fails
        with patch('main.train', wraps=train) as learning:
            code, logs = self.run_job(reader=reader)
        self.assertEqual(code, 0)
        self.assertEqual(events, ['Gold', 'POST0', 'POST0', 'Gold', 'POST1'])
        self.assertEqual([len(x) for x in self.client.writes], [0, 0, 1])
        self.assertEqual(learning.call_count, 1)

    def test_two_batches_resume_only_second_without_retraining(self):
        ids = [str(UUID(int=i + 1)) for i in range(201)]
        client = Client(ids)
        def failure(client, items):
            if len(client.writes) == 2:
                raise JobError('api_rejected_request')
            return acknowledgement(items)['items']
        client.on_write = failure
        rows = [row(ad_group_id=ident) for ident in ids]
        with patch('main.train', wraps=train) as learning:
            self.assertEqual(self.run_job(rows, client=client)[0], 1)
            self.assertEqual(self.state()['pending']['offset'], 200)
            first_second = deepcopy(client.writes[1])
            self.assertEqual(self.run_job(reader=lambda *args: self.fail('Gold reread'), client=client)[0], 0)
        self.assertEqual(learning.call_count, 1)
        self.assertEqual([len(x) for x in client.writes], [200, 1, 1])
        self.assertEqual(client.writes[2], first_second)
        self.assertEqual(self.state()['model_version'], 'v000001')

    def test_inflight_save_failure_prevents_first_post(self):
        store = Store(self.settings.state_dir, self.settings.identity)
        original = store.save
        def fail_inflight(state):
            if state['pending'] and state['pending']['inflight']:
                raise JobError('state_write_failed')
            original(state)
        with patch.object(store, 'save', side_effect=fail_inflight):
            code, logs = self.run_job(store=store)
        self.assertEqual(code, 1)
        self.assertEqual(self.client.writes, [])
        self.assertEqual(logs[-1]['trained_rows'], 1)
        with patch('main.train') as learning:
            self.assertEqual(self.run_job()[0], 0)
        learning.assert_not_called()

    def test_response_loss_retries_identical_body_no_new_model(self):
        def lost(client, items):
            if len(client.writes) == 1:
                raise JobError('api_connection_failed', retryable=True)
            return acknowledgement(items, 'unchanged')['items']
        self.client.on_write = lost
        with patch('main.train', wraps=train) as learning:
            code, logs = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual(learning.call_count, 1)
        self.assertEqual(self.client.writes[0], self.client.writes[1])
        self.assertEqual(logs[-1]['write_counts'], {'unchanged': 1})
        self.assertEqual(self.clock.waits, [600])

    def test_success_then_local_save_failure_resumes_identical_body(self):
        store = Store(self.settings.state_dir, self.settings.identity)
        original = store.save
        failed = False
        def fail_progress(state):
            nonlocal failed
            pending = state['pending']
            if pending and pending['offset'] == 1 and pending['inflight'] is None and not failed:
                failed = True
                raise JobError('state_write_failed')
            original(state)
        with patch.object(store, 'save', side_effect=fail_progress):
            self.assertEqual(self.run_job(store=store)[0], 1)
        self.assertIsNotNone(self.state()['pending']['inflight'])
        with patch('main.train') as learning:
            self.assertEqual(self.run_job()[0], 0)
        learning.assert_not_called()
        self.assertEqual(self.client.writes[0], self.client.writes[1])

    def test_frozen_inflight_change_blocks_without_subset_rewrite(self):
        for missing in (False, True):
            settings = replace(self.settings, state_dir=self.root / f'blocked-{missing}')
            client, clock = Client(), Clock()
            def first_failure(client, items):
                if missing:
                    client.ads.clear()
                else:
                    client.ads[ID]['profile']['category'] = 4
                raise JobError('api_connection_failed', retryable=True)
            client.on_write = first_failure
            self.assertEqual(self.run_job(settings=settings, client=client, clock=clock)[0], 75)
            self.assertEqual(len(client.writes), 1)
            self.assertTrue(self.state(settings)['pending']['blocked'])
            client.ads = Client().ads
            self.assertEqual(self.run_job(settings=settings, client=client, clock=clock)[0], 75)
            self.assertEqual(len(client.writes), 1)

    def test_unsent_change_and_initial_exclusion_send_empty_audit(self):
        for stage in ('before_learning', 'before_delivery'):
            settings = replace(self.settings, state_dir=self.root / stage)
            client = Client()
            def change(client):
                if client.reads >= (1 if stage == 'before_learning' else 2):
                    client.ads[ID]['profile']['category'] = 0
            client.on_list = change
            code, logs = self.run_job(settings=settings, client=client)
            self.assertEqual(code, 0)
            self.assertEqual(client.writes, [[]])
            self.assertEqual(logs[-1]['trained_rows'], 1)
            self.assertEqual(logs[-1]['write_excluded_rows'], 1)
            self.assertEqual(logs[-1]['write_excluded_ad_group_ids'], [ID])

    def test_old_pending_archived_before_new_gold_failure(self):
        self.client.on_write = lambda *args: (_ for _ in ()).throw(JobError('api_rejected_request'))
        self.assertEqual(self.run_job()[0], 1)
        self.clock.value += timedelta(days=1)
        count = len(self.client.writes)
        def broken(*args):
            raise JobError('gold_schema_mismatch')
        self.assertEqual(self.run_job(reader=broken)[0], 1)
        self.assertEqual(len(self.client.writes), count)
        self.assertIsNone(self.state()['pending'])
        archived = json.loads((self.settings.state_dir / 'runs/2026-11-11_superseded.json').read_text())
        self.assertIsNotNone(archived['superseded_pending']['inflight'])

    def test_retry_budget_delay_deadline_and_fixed_target(self):
        calls = []
        def unavailable(uri, day, options):
            calls.append(day)
            raise JobError('gold_connection_failed', retryable=True, retry_after=900)
        code, logs = self.run_job(reader=unavailable)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [DAY] * 3)
        self.assertEqual(self.clock.waits, [900, 900])
        self.assertEqual(self.client.writes, [])
        clock = Clock(datetime(2026, 11, 12, 14, 55, tzinfo=timezone.utc))
        code, logs = self.run_job(reader=unavailable, clock=clock)
        self.assertEqual(code, 75)
        self.assertEqual(clock.waits, [])
        self.assertEqual(logs[-1]['code'], 'retry_would_cross_kst_date')

    def test_permanent_auth_failure_before_learning(self):
        def denied(client):
            raise JobError('api_rejected_request', details={'http_status': 403})
        self.client.on_list = denied
        with patch('main.train') as learning:
            self.assertEqual(self.run_job()[0], 1)
        learning.assert_not_called()
        self.assertEqual(self.clock.waits, [])
        self.assertEqual(self.client.writes, [])
        self.assertIsNone(self.state()['model_version'])

    def test_commit_failure_reports_zero_trained_rows(self):
        store = Store(self.settings.state_dir, self.settings.identity)
        with patch.object(store, 'publish_model', side_effect=JobError('model_publish_failed')):
            code, logs = self.run_job(store=store)
        self.assertEqual(code, 1)
        self.assertEqual(logs[-1]['trained_rows'], 0)
        self.assertIsNone(self.state()['model_version'])
        self.assertEqual(self.client.writes, [])

    def test_environment_and_runtime_guard_before_joblib_and_network(self):
        self.assertEqual(self.run_job()[0], 0)
        before = (self.client.reads, len(self.client.writes))
        for settings in (replace(self.settings, ads_base_url='https://other.example.com/api/internal/v1'),
                         replace(self.settings, gold_uri='s3://other/root'),
                         replace(self.settings, storage_options={'AWS_ENDPOINT_URL': 'http://other:9000'})):
            with patch('state.joblib.load') as loading:
                self.assertEqual(self.run_job(settings=settings, reader=lambda *args: self.fail('Gold access'))[0], 1)
            loading.assert_not_called()
        state = self.state()
        state['runtime']['sklearn'] = 'wrong'
        Store(self.settings.state_dir, self.settings.identity).save(state)
        with patch('state.joblib.load') as loading:
            self.assertEqual(self.run_job(reader=lambda *args: self.fail('Gold access'))[0], 1)
        loading.assert_not_called()
        self.assertEqual((self.client.reads, len(self.client.writes)), before)

    def test_key_rotation_and_dry_run(self):
        code, logs = self.run_job(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(logs[-1]['status'], 'dry_run')
        self.assertEqual(logs[-1]['trained_rows'], 0)
        self.assertEqual(self.client.writes, [])
        self.assertIsNone(self.state()['model_version'])
        self.assertFalse((self.settings.state_dir / 'models').exists())
        self.assertEqual(self.run_job(settings=replace(self.settings, ads_key='ROTATED'))[0], 0)

    def test_expired_after_training_does_not_commit_or_post(self):
        original = main.predict
        def expired(*args):
            result = original(*args)
            self.clock.value = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)
            return result
        with patch('main.predict', side_effect=expired):
            code, logs = self.run_job()
        self.assertEqual(code, 75)
        self.assertEqual(self.client.writes, [])
        self.assertIsNone(self.state()['model_version'])
        self.assertEqual(logs[-1]['trained_rows'], 0)

    def test_cli_exit_codes_and_safe_setting_failure(self):
        for code in (0, 75, 1):
            with patch('main.Settings.from_env', return_value=self.settings), patch('main.execute', return_value=code):
                self.assertEqual(main.main(['--dry-run']), code)
        output = io.StringIO()
        with patch('main.Settings.from_env', side_effect=ValueError('NEVER_STORE_THIS_KEY')), redirect_stdout(output):
            self.assertEqual(main.main([]), 1)
        self.assertNotIn('NEVER_STORE_THIS_KEY', output.getvalue())
