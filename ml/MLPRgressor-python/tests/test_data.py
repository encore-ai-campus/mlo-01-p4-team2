from datetime import date
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import pyarrow as pa
from deltalake import write_deltalake

from data import features, prepare, read_gold, uuid_text
from errors import JobError
from helpers import DAY, ID, arrow_table, row


class DataTests(unittest.TestCase):
    def test_bit_order_and_click_counts_unused(self):
        self.assertEqual(features(row())[6:], [0, 0, 1, 1, 0, 0, 0, 0])
        self.assertEqual(features(row(category=1))[6:], [1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(features(row(category=128))[6:], [0, 0, 0, 0, 0, 0, 0, 1])
        self.assertEqual(features(row(category=0))[6:], [0] * 8)
        self.assertEqual(features(row()), features(row(mlo_clicks=10**9)))
        self.assertEqual(len(features(row())), 14)

    def test_invalid_values(self):
        for change in ({'category': None}, {'category': True}, {'category': 256}, {'category': -1},
                       {'mlo_ctr': None}, {'mlo_ctr': True}, {'aio_cvr': float('nan')},
                       {'skn_cvr': float('inf')}, {'mlo_ctr': -.1}, {'aio_ctr': 1.01}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                features(row(**change))

    def test_uuid_strict(self):
        self.assertEqual(uuid_text(ID.upper()), ID)
        for bad in ('urn:' + '1' * 32, '1' * 32, None, '{' + ID + '}'):
            with self.assertRaises(ValueError):
                uuid_text(bad)

    def test_distinct_hold_reasons(self):
        self.assertEqual(prepare([], DAY, 0)[2], 'gold_empty')
        self.assertEqual(prepare([row(confirmed=False)], DAY, 1)[2], 'gold_unconfirmed')
        self.assertEqual(prepare([row(category=None)], DAY, 2)[2], 'no_valid_rows')
        self.assertEqual(prepare([row(mlo_cvr=None)], DAY, 3)[2], 'no_valid_rows')
        self.assertIsNone(prepare([row()], DAY, 4)[2])

    def test_stats_and_unidentified(self):
        items = [row(), row(ad_group_id=str(UUID(int=2)), category=None), row(ad_group_id='not-a-uuid')]
        valid, stats, reason = prepare(items, DAY, 5)
        self.assertEqual(stats['planned_rows'], stats['valid_rows'] + stats['invalid_rows'])
        self.assertEqual((len(valid), stats['invalid_rows'], stats['unidentified_rows']), (1, 2, 1))
        self.assertEqual(stats['invalid_ad_group_ids'], [str(UUID(int=2))])
        self.assertEqual(stats['trained_rows'], 0)
        self.assertIsNone(reason)

    def test_duplicate_wrong_day_and_bool_type(self):
        for items in ([row(), row(ad_group_id=ID.upper())], [row(kst_date=date(2026, 11, 10))], [row(confirmed=None)]):
            with self.assertRaises(JobError):
                prepare(items, DAY, 1)

    def test_actual_delta_snapshot_and_date_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            uri = str(Path(directory) / 'gold')
            write_deltalake(uri, arrow_table([row(), row(kst_date=date(2026, 11, 10))]))
            rows, version = read_gold(uri, DAY, {})
            self.assertEqual(version, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['kst_date'], DAY)
            self.assertNotIn('mlo_clicks', rows[0])

    def test_schema_error(self):
        with tempfile.TemporaryDirectory() as directory:
            uri = str(Path(directory) / 'gold')
            write_deltalake(uri, arrow_table([row()], category_type=pa.int64()))
            with self.assertRaises(JobError) as caught:
                read_gold(uri, DAY, {})
            self.assertEqual(caught.exception.code, 'gold_schema_mismatch')

    def test_network_error_classification(self):
        with patch('deltalake.DeltaTable', side_effect=socket.gaierror('SECRET_HOST')), self.assertRaises(JobError) as caught:
            read_gold('s3://bucket/root', DAY, {})
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn('SECRET_HOST', str(caught.exception))
        with patch('deltalake.DeltaTable', side_effect=RuntimeError('SECRET_CREDENTIALS')), self.assertRaises(JobError) as caught:
            read_gold('s3://bucket/root', DAY, {})
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn('SECRET_CREDENTIALS', str(caught.exception.details))
