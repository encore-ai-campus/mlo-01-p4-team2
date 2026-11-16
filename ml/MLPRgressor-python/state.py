"""Local model versions and atomic progress state; single STATE_DIR writer."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import joblib
import numpy as np
import sklearn

from data import FEATURES
from errors import JobError
from model import TARGET_RULE

RUNTIME = {'python': f'{sys.version_info.major}.{sys.version_info.minor}',
           'sklearn': sklearn.__version__, 'numpy': np.__version__, 'joblib': joblib.__version__}
VERSION = re.compile(r'v[0-9]{6,}')


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_directory(directory):
    if not directory.exists():
        ensure_directory(directory.parent)
    directory.mkdir(exist_ok=True, mode=0o700)
    # Also sync existing entries so a retry repairs a failed directory sync.
    sync_directory(directory.parent)


def atomic_write(path, value, *, binary=False, immutable=False):
    ensure_directory(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            if binary:
                joblib.dump(value, handle)
            else:
                handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
            handle.flush()
            os.fsync(handle.fileno())
        if immutable:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reject_constant(value):
    raise ValueError('nonstandard_json_number')


class Store:
    def __init__(self, root, identity):
        self.root = Path(root)
        self.identity = dict(identity)

    @contextmanager
    def locked(self):
        ensure_directory(self.root)
        with (self.root / 'run.lock').open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobError('another_job_is_running', deferred=True) from None
            yield

    def load(self):
        path = self.root / 'state.json'
        if not path.exists():
            state = {'format': 1, 'environment': self.identity, 'features': list(FEATURES),
                     'target_rule': TARGET_RULE, 'runtime': dict(RUNTIME), 'model_version': None,
                     'trained_date': None, 'completed_date': None, 'pending': None, 'last_report': None}
            self.save(state)
            return state
        try:
            state = json.loads(path.read_text(), parse_constant=reject_constant)
            if not isinstance(state, dict):
                raise ValueError('invalid_state')
            if state.get('environment') != self.identity:
                raise JobError('environment_mismatch_use_separate_state_dir')
            if (state.get('format') != 1 or state.get('features') != list(FEATURES)
                    or state.get('target_rule') != TARGET_RULE):
                raise JobError('state_contract_mismatch')
            for name in ('trained_date', 'completed_date'):
                if state[name] is not None and not valid_date(state[name]):
                    raise ValueError('invalid_state_date')
            version = state['model_version']
            if version is None:
                if state['trained_date'] is not None or state['completed_date'] is not None or state['pending'] is not None:
                    raise ValueError('uncommitted_model_reference')
            else:
                if not isinstance(version, str) or not VERSION.fullmatch(version) or state['trained_date'] is None:
                    raise ValueError('invalid_model_version')
                if state.get('runtime') != RUNTIME:
                    raise JobError('model_runtime_mismatch')
                if state['completed_date'] and state['completed_date'] > state['trained_date']:
                    raise ValueError('invalid_completion_order')
                pending = state['pending']
                if pending is not None and (not isinstance(pending, dict) or pending.get('date') != state['trained_date']
                                            or pending.get('model_version') != version):
                    raise ValueError('pending_model_mismatch')
            return state
        except JobError:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError, OSError):
            raise JobError('state_read_failed') from None

    def save(self, state):
        try:
            atomic_write(self.root / 'state.json', state)
        except (OSError, ValueError, TypeError):
            raise JobError('state_write_failed') from None

    def load_model(self, state):
        version = state['model_version']
        if version is None:
            return None
        if not isinstance(version, str) or not VERSION.fullmatch(version) or state.get('runtime') != RUNTIME:
            raise JobError('model_runtime_or_version_mismatch')
        try:
            # Only our own trusted files: joblib deserialization can execute code.
            bundle = joblib.load(self.root / 'models' / f'{version}.joblib')
            metadata = bundle['metadata']
            if (metadata['runtime'] != RUNTIME or metadata['features'] != list(FEATURES)
                    or metadata['target_rule'] != TARGET_RULE or metadata['environment'] != self.identity
                    or metadata['version'] != version or metadata['trained_date'] != state['trained_date']):
                raise JobError('model_history_mismatch')
            return bundle['model']
        except JobError:
            raise
        except Exception as error:
            raise JobError('model_load_failed', details={'error_type': type(error).__name__}) from None

    def publish_model(self, model, state, stats, epochs, pending):
        directory = self.root / 'models'
        numbers = [int(p.stem[1:]) for p in directory.glob('v*.joblib') if VERSION.fullmatch(p.stem)]
        version = f'v{max(numbers, default=0) + 1:06d}'
        metadata = {'version': version, 'previous_version': state['model_version'],
                    'runtime': dict(RUNTIME), 'features': list(FEATURES), 'target_rule': TARGET_RULE,
                    'environment': self.identity, 'trained_date': stats['kst_date'],
                    'training_stats': stats, 'epochs': epochs,
                    'saved_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
        try:
            atomic_write(directory / f'{version}.joblib', {'model': model, 'metadata': metadata},
                         binary=True, immutable=True)
        except (OSError, ValueError, TypeError):
            raise JobError('model_publish_failed') from None
        updated = {**state, 'model_version': version, 'trained_date': stats['kst_date'], 'runtime': dict(RUNTIME),
                   'pending': {**pending, 'model_version': version}}
        self.save(updated)
        state.clear()
        state.update(updated)
        return version

    def write_report(self, report):
        if not valid_date(report['kst_date']) or report['status'] not in ('completed', 'deferred', 'superseded', 'failed'):
            raise JobError('invalid_report_identity')
        try:
            atomic_write(self.root / 'runs' / f"{report['kst_date']}_{report['status']}.json", report)
        except (OSError, ValueError, TypeError):
            raise JobError('report_write_failed') from None

    def report(self, state, report):
        self.write_report(report)
        updated = {**state, 'last_report': report}
        self.save(updated)
        state.clear()
        state.update(updated)
