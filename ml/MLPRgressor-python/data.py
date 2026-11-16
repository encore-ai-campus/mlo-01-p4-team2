"""Gold snapshot input contract; no API calls or model writes."""
from collections import Counter
from datetime import date
import errno
import math
import re
import socket
from uuid import UUID

from errors import JobError

COURSES = ('mlo', 'aio', 'skn')
FEATURES = tuple(f'{c}_{r}' for c in COURSES for r in ('ctr', 'cvr')) + tuple(f'category_bit_{i}' for i in range(8))
UUID_PATTERN = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


def uuid_text(value):
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        raise ValueError('invalid_uuid')
    return str(UUID(value))


def features(row):
    if row.get('confirmed') is not True:
        raise ValueError('unconfirmed')
    category = row.get('category')
    if category is None:
        raise ValueError('category_missing')
    if type(category) is not int or not 0 <= category <= 255:
        raise ValueError('invalid_category')
    rates = [row.get(name) for name in FEATURES[:6]]
    if any(v is None for v in rates):
        raise ValueError('rate_missing')
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in rates):
        raise ValueError('invalid_rate')
    return rates + [(category >> bit) & 1 for bit in range(8)]


def prepare(rows, day, version):
    valid, invalid_ids, ids = [], [], set()
    reasons = Counter()
    unidentified = 0
    unconfirmed = False
    for row in rows:
        if type(row.get('kst_date')) is not date or row['kst_date'] != day:
            raise JobError('unexpected_gold_date')
        if type(row.get('confirmed')) is not bool:
            raise JobError('invalid_gold_confirmed_type')
        unconfirmed |= not row['confirmed']
        try:
            ident = uuid_text(row.get('ad_group_id'))
        except ValueError:
            unidentified += 1
            reasons['invalid_id'] += 1
            continue
        if ident in ids:
            raise JobError('duplicate_gold_key')
        ids.add(ident)
        try:
            x = features(row)
        except ValueError as error:
            invalid_ids.append(ident)
            reasons[str(error)] += 1  # features() only emits fixed codes.
            continue
        valid.append((ident, row['category'], x))
    stats = {'kst_date': day.isoformat(), 'gold_version': version, 'planned_rows': len(rows),
             'valid_rows': len(valid), 'trained_rows': 0, 'invalid_rows': len(rows) - len(valid),
             'invalid_ad_group_ids': invalid_ids, 'unidentified_rows': unidentified,
             'invalid_reason_counts': dict(reasons)}
    reason = 'gold_empty' if not rows else 'gold_unconfirmed' if unconfirmed else 'no_valid_rows' if not valid else None
    return valid, stats, reason


def read_gold(uri, day, storage_options):
    from deltalake import DeltaTable
    import pyarrow as pa
    import pyarrow.dataset as ds
    try:
        table = DeltaTable(uri, storage_options=storage_options)
        dataset = table.to_pyarrow_dataset()
        expected = {'kst_date': (pa.date32(),), 'ad_group_id': (pa.string(), pa.large_string()),
                    'category': (pa.int32(),), 'confirmed': (pa.bool_(),),
                    **{name: (pa.float64(),) for name in FEATURES[:6]}}
        for name, types in expected.items():
            if name not in dataset.schema.names or dataset.schema.field(name).type not in types:
                raise JobError('gold_schema_mismatch', details={'column': name})
        rows = dataset.scanner(columns=list(expected), filter=ds.field('kst_date') == pa.scalar(day)).to_table().to_pylist()
        return rows, table.version()
    except JobError:
        raise
    except (TimeoutError, ConnectionError, socket.gaierror):
        raise JobError('gold_connection_failed', retryable=True) from None
    except OSError as error:
        temporary = error.errno in (errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ENETUNREACH, errno.EHOSTUNREACH)
        raise JobError('gold_read_failed', retryable=temporary, details={'error_type': type(error).__name__}) from None
    except Exception as error:
        raise JobError('gold_read_failed', details={'error_type': type(error).__name__}) from None
