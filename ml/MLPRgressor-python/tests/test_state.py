import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import joblib

from data import features
from errors import JobError
from model import train
from state import RUNTIME, Store, atomic_write, sync_directory
from helpers import DAY, row
from test_settings import config


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = config()
        self.store = Store(self.root, self.settings.identity)

    def publish(self, state, day=DAY.isoformat()):
        model = train(self.store.load_model(state), [features(row())], 2)
        stats = dict(kst_date=day, gold_version=1, planned_rows=1, valid_rows=1, trained_rows=1, invalid_rows=0)
        pending = dict(date=day, outputs=[], offset=0, inflight=None, blocked=False,
                       empty_sent=False, stats=stats, write_counts={})
        version = self.store.publish_model(model, state, stats, 2, pending)
        return version

    def test_versions_and_learning_history(self):
        with self.store.locked():
            state = self.store.load()
            self.assertIsNone(self.store.load_model(state))
            self.assertEqual(self.publish(state), 'v000001')
            first = self.root / 'models/v000001.joblib'
            before = hashlib.sha256(first.read_bytes()).hexdigest()
            model1 = self.store.load_model(state)
            self.assertEqual(self.publish(state, '2026-11-12'), 'v000002')
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), before)
            model2 = self.store.load_model(self.store.load())
            self.assertGreater(model2.t_, model1.t_)
            bundle = joblib.load(self.root / 'models/v000002.joblib')
            self.assertEqual(bundle['metadata']['previous_version'], 'v000001')
            self.assertEqual(bundle['metadata']['training_stats']['trained_rows'], 1)
            self.assertNotIn('NEVER_STORE_THIS_KEY', str(bundle['metadata']))

    def test_environment_mismatch_before_model_load(self):
        with self.store.locked():
            state = self.store.load()
            self.publish(state)
        for settings in (replace(self.settings, ads_base_url='https://other.example.com/api/internal/v1'),
                         replace(self.settings, gold_uri='s3://other/root'),
                         replace(self.settings, storage_options={'AWS_ENDPOINT_URL': 'http://other.internal:9000'})):
            other = Store(self.root, settings.identity)
            with patch('state.joblib.load') as load, self.assertRaises(JobError) as caught:
                other.load()
            self.assertEqual(caught.exception.code, 'environment_mismatch_use_separate_state_dir')
            load.assert_not_called()
        rotated = Store(self.root, replace(self.settings, ads_key='ROTATED_KEY').identity)
        self.assertEqual(rotated.load()['model_version'], 'v000001')

    def test_runtime_mismatch_before_joblib(self):
        with self.store.locked():
            state = self.store.load()
            self.publish(state)
            state['runtime'] = {**RUNTIME, 'sklearn': 'wrong-version'}
            self.store.save(state)
        with patch('state.joblib.load') as load, self.assertRaises(JobError):
            self.store.load()
        load.assert_not_called()

    def test_model_published_but_pointer_not_changed(self):
        with self.store.locked():
            state = self.store.load()
            self.publish(state)
            before = copy.deepcopy(state)
            with patch.object(self.store, 'save', side_effect=JobError('state_write_failed')), self.assertRaises(JobError):
                self.publish(state, '2026-11-12')
            self.assertEqual(state, before)
            self.assertEqual(self.store.load()['model_version'], 'v000001')
            self.assertTrue((self.root / 'models/v000002.joblib').exists())
            self.assertEqual(self.publish(state, '2026-11-12'), 'v000003')

    def test_directory_sync_failure_before_pointer_update(self):
        with self.store.locked():
            state = self.store.load()
            with patch('state.sync_directory', side_effect=OSError('simulated')), self.assertRaises(JobError):
                self.publish(state)
            self.assertIsNone(self.store.load()['model_version'])

    def test_pointer_failure_before_replace_preserves_old(self):
        with self.store.locked():
            state = self.store.load()
            updated = {**state, 'last_report': {'status': 'deferred'}}
            with patch('state.os.replace', side_effect=OSError('simulated')), self.assertRaises(JobError):
                self.store.save(updated)
            self.assertIsNone(self.store.load()['last_report'])

    def test_pointer_directory_sync_failure_leaves_complete_new_pointer(self):
        with self.store.locked():
            state = self.store.load()
            updated = {**state, 'last_report': {'status': 'deferred'}}
            def fail_after_replace(path):
                if path == self.root:
                    raise OSError('simulated')
                sync_directory(path)
            with patch('state.sync_directory', side_effect=fail_after_replace), self.assertRaises(JobError):
                self.store.save(updated)
            self.assertEqual(self.store.load()['last_report'], updated['last_report'])

    def test_new_state_directory_sync_failure_prevents_lock(self):
        root = self.root / 'nested/state'
        store = Store(root, self.settings.identity)
        with patch('state.sync_directory', side_effect=OSError('simulated')), self.assertRaises(OSError):
            with store.locked():
                self.fail('lock entered before parent sync')
        self.assertFalse((root / 'run.lock').exists())
        with store.locked():
            self.assertIsNone(store.load()['model_version'])

    def test_new_model_directory_synced_before_pointer_replace(self):
        with self.store.locked():
            state = self.store.load()
            events = []
            from state import os
            original_replace = os.replace
            def record_sync(path):
                events.append(('sync', path))
                sync_directory(path)
            def record_replace(source, target):
                events.append(('replace', target))
                original_replace(source, target)
            with patch('state.sync_directory', side_effect=record_sync), patch('state.os.replace', side_effect=record_replace):
                self.publish(state)
            pointer = events.index(('replace', self.root / 'state.json'))
            self.assertIn(('sync', self.root), events[:pointer])
            self.assertIn(('sync', self.root / 'models'), events[:pointer])
            self.assertEqual(events[-1], ('sync', self.root))

    def test_existing_model_version_is_never_overwritten(self):
        file = self.root / 'models/v000001.joblib'
        atomic_write(file, {'test': 'original'}, binary=True, immutable=True)
        before = file.read_bytes()
        with self.assertRaises(FileExistsError):
            atomic_write(file, {'test': 'changed'}, binary=True, immutable=True)
        self.assertEqual(file.read_bytes(), before)

    def test_lock_competition(self):
        with self.store.locked():
            with self.assertRaises(JobError) as caught:
                with Store(self.root, self.settings.identity).locked():
                    self.fail('second lock acquired')
            self.assertTrue(caught.exception.deferred)

    def test_corrupt_state_and_invalid_model_path_fail_closed(self):
        self.store.load()
        path = self.root / 'state.json'
        path.write_text('{broken')
        with self.assertRaises(JobError):
            self.store.load()
        state = dict(format=1, environment=self.settings.identity, features=[], target_rule='wrong')
        path.write_text(json.dumps(state))
        with self.assertRaises(JobError):
            self.store.load()

    def test_no_secrets_in_saved_state(self):
        with self.store.locked():
            state = self.store.load()
            self.publish(state)
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'NEVER_STORE_THIS_KEY', path.read_bytes())
