"""Daily standalone MLP weights client; no API server or direct DB access."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, time as wall_time, timezone
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from ads import AdsClient, WEIGHT_FIELDS
from data import prepare, read_gold
from errors import JobError
from model import predict, train, weights
from settings import ROOT, Settings
from state import Store

KST = ZoneInfo('Asia/Seoul')


def utcnow():
    return datetime.now(timezone.utc)


def emit(report):
    print(json.dumps({'source': 'mlpregressor', **report}, ensure_ascii=False, allow_nan=False), flush=True)


def commit(store, state, **changes):
    updated = {**state, **changes}
    store.save(updated)
    state.clear()
    state.update(updated)


def current_matches(ads, ident, category):
    profile = ads.get(ident, {}).get('profile')
    return profile is not None and profile['category'] == category


def exclude(stats, ident):
    ids = stats.setdefault('write_excluded_ad_group_ids', [])
    if ident not in ids:
        ids.append(ident)
    stats['write_excluded_rows'] = len(ids)


def deliver(client, store, state, empty_audit):
    pending = deepcopy(state['pending'])
    if pending['blocked']:
        raise JobError('publish_state_changed', deferred=True)
    current = client.list_ads()
    # shortcut: C8 has no atomic version check; run one weights writer until the contract adds one.
    while pending['inflight'] is not None or pending['offset'] < len(pending['outputs']):
        inflight = pending['inflight']
        if inflight is None:
            end = min(pending['offset'] + 200, len(pending['outputs']))
            items, categories = [], {}
            for output in pending['outputs'][pending['offset']:end]:
                ident = output['ad_group_id']
                if not current_matches(current, ident, output['category']):
                    exclude(pending['stats'], ident)
                    pending['write_counts']['skipped_current_state'] = pending['write_counts'].get('skipped_current_state', 0) + 1
                    continue
                items.append({'ad_group_id': ident, **output['weights']})
                categories[ident] = output['category']
            if not items:
                pending['offset'] = end
                commit(store, state, pending=deepcopy(pending))
                continue
            inflight = {'items': items, 'categories': categories, 'end_offset': end}
            pending['inflight'] = inflight
            # A crash or lost response must preserve the exact first request body.
            commit(store, state, pending=deepcopy(pending))
        if any(not current_matches(current, ident, category) for ident, category in inflight['categories'].items()):
            pending['blocked'] = True
            commit(store, state, pending=deepcopy(pending))
            raise JobError('publish_state_changed', deferred=True)
        response = client.write_weights(inflight['items'])
        counts = Counter(pending['write_counts'])
        counts.update(item['result'] for item in response)
        pending.update(offset=inflight['end_offset'], inflight=None, write_counts=dict(counts))
        commit(store, state, pending=deepcopy(pending))
    if not any(pending['write_counts'].get(k, 0) for k in ('created', 'changed', 'unchanged')) and not pending['empty_sent']:
        empty_audit()
        pending['empty_sent'] = True
        commit(store, state, pending=deepcopy(pending))
    report = {**pending['stats'], 'source': 'mlpregressor', 'status': 'completed',
              'model_version': pending['model_version'], 'write_counts': pending['write_counts'],
              'request_ids': list(client.request_ids)}
    store.write_report(report)
    commit(store, state, completed_date=pending['date'], pending=None, last_report=report)
    return report


def run_once(settings, client, store, state, day, empty_audit, context, *, reader, dry_run, check_deadline):
    stamp = day.isoformat()
    if state['completed_date'] and stamp <= state['completed_date']:
        return {'status': 'already_completed', 'kst_date': stamp, 'model_version': state['model_version']}
    pending = state['pending']
    if pending is not None:
        if pending['date'] > stamp:
            raise JobError('requested_date_precedes_pending_work')
        if dry_run:
            raise JobError('pending_delivery_exists', deferred=True)
        if pending['date'] == stamp:
            return deliver(client, store, state, empty_audit)
        summary = {**pending['stats'], 'source': 'mlpregressor', 'status': 'superseded',
                   'model_version': pending['model_version'], 'write_counts': pending['write_counts']}
        store.write_report({**summary, 'superseded_pending': pending})
        commit(store, state, pending=None, last_report=summary)
    if state['trained_date'] and stamp <= state['trained_date']:
        raise JobError('training_date_already_committed')
    check_deadline()
    rows, version = reader(settings.gold_uri, day, settings.storage_options)
    valid, stats, reason = prepare(rows, day, version)
    context['stats'] = stats
    if reason:
        if not dry_run:
            empty_audit()
        raise JobError(reason, retryable=reason in ('gold_empty', 'gold_unconfirmed'), deferred=True)
    ads = client.list_ads()  # Fail authentication before doing expensive learning.
    x = [item[2] for item in valid]
    learned = train(store.load_model(state), x, settings.train_epochs)
    predictions = predict(learned, x)
    outputs = []
    stats.update(write_excluded_rows=0, write_excluded_ad_group_ids=[])
    for (ident, category, _), prediction in zip(valid, predictions):
        if not current_matches(ads, ident, category):
            exclude(stats, ident)
            continue
        outputs.append({'ad_group_id': ident, 'category': category,
                        'weights': dict(zip(WEIGHT_FIELDS, weights(prediction)))})
    check_deadline()
    if dry_run:
        return {**stats, 'status': 'dry_run', 'preview_outputs': outputs, 'model_version': state['model_version']}
    committed_stats = {**stats, 'trained_rows': len(valid)}
    pending = {'date': stamp, 'outputs': outputs, 'offset': 0, 'inflight': None, 'blocked': False,
               'empty_sent': False, 'stats': committed_stats, 'write_counts': {}}
    store.publish_model(learned, state, committed_stats, settings.train_epochs, pending)
    context['stats'] = committed_stats
    return deliver(client, store, state, empty_audit)


def execute(settings, *, client=None, store=None, reader=read_gold, dry_run=False,
            clock=utcnow, sleep=time.sleep):
    started = clock().astimezone(KST)
    day = started.date() - timedelta(days=1)
    expires_at = datetime.combine(started.date() + timedelta(days=1), wall_time.min, KST).astimezone(timezone.utc)
    client = client if client is not None else AdsClient(settings, expires_at=expires_at, now=clock)
    client.expires_at = expires_at
    store = store if store is not None else Store(settings.state_dir, settings.identity)
    empty_sent = False
    empty_inflight = False

    def check_deadline():
        if clock() >= expires_at:
            raise JobError('target_date_expired', deferred=True)

    def empty_audit():
        nonlocal empty_sent, empty_inflight
        if not empty_sent:
            check_deadline()
            empty_inflight = True
            client.write_weights([])
            empty_inflight = False
            empty_sent = True

    try:
        with store.locked():
            for attempt in range(1, 4):
                context, state = {}, None
                try:
                    state = store.load()  # Source/runtime guard before network and joblib.
                    check_deadline()
                    if empty_inflight:
                        empty_audit()
                    report = run_once(settings, client, store, state, day, empty_audit, context,
                                      reader=reader, dry_run=dry_run, check_deadline=check_deadline)
                    emit({**report, 'attempt': attempt})
                    return 0
                except Exception as caught:
                    error = caught if isinstance(caught, JobError) else JobError(
                        'unexpected_error', details={'error_type': type(caught).__name__})
                    stats = context.get('stats', {})
                    # A save can succeed at rename then fail at fsync; consult its complete on-disk pointer.
                    try:
                        if state is not None:
                            state = store.load()
                            if state['pending'] and state['pending']['date'] == day.isoformat():
                                stats = {**state['pending']['stats'], 'write_counts': state['pending']['write_counts'],
                                         'delivery_uncertain': state['pending']['inflight'] is not None}
                            elif state['completed_date'] == day.isoformat():
                                stats = state['last_report']
                    except JobError:
                        state = None
                    report = {**stats, 'source': 'mlpregressor', 'kst_date': day.isoformat(),
                              'status': 'deferred' if error.deferred else 'failed', 'code': error.code,
                              'attempt': attempt, 'model_version': state['model_version'] if state else None,
                              'request_id': error.request_id, 'request_ids': list(client.request_ids),
                              'diagnostics': error.details}
                    if state is not None and not dry_run:
                        try:
                            store.report(state, report)
                        except JobError as record_error:
                            emit({**report, 'status': 'failed', 'record_code': record_error.code})
                            return 1
                    emit(report)
                    if dry_run or not error.retryable or attempt == 3:
                        return 75 if error.deferred else 1
                    delay = max(600, error.retry_after)
                    if delay >= (expires_at - clock()).total_seconds():
                        terminal = {**report, 'status': 'deferred', 'code': 'retry_would_cross_kst_date'}
                        if state is not None and not dry_run:
                            store.report(state, terminal)
                        emit(terminal)
                        return 75
                    emit({'status': 'retry_wait', 'seconds': delay, 'attempt': attempt, 'kst_date': day.isoformat()})
                    sleep(delay)
    except Exception as caught:
        error = caught if isinstance(caught, JobError) else JobError('unexpected_error', details={'error_type': type(caught).__name__})
        emit({'status': 'deferred' if error.deferred else 'failed', 'kst_date': day.isoformat(),
              'code': error.code, 'diagnostics': error.details})
        return 75 if error.deferred else 1
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--dry-run', action='store_true', help='Read and preview; no POST or saved model version')
    args = parser.parse_args(argv)
    try:
        return execute(Settings.from_env(args.env_file), dry_run=args.dry_run)
    except Exception as caught:
        error = caught if isinstance(caught, JobError) else JobError('unexpected_error', details={'error_type': type(caught).__name__})
        emit({'status': 'failed', 'code': error.code, 'diagnostics': error.details})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
