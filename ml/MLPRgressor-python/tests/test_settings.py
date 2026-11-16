from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from errors import JobError
from settings import Settings, ROOT


def config(**changes):
    return Settings(**{**dict(ads_base_url='https://ads.example.com/api/internal/v1', ads_key='NEVER_STORE_THIS_KEY',
                             gold_uri='s3://bucket/gold/ads/creative_course_daily/v2'), **changes})


class SettingsTests(unittest.TestCase):
    def test_normalize_and_secrets(self):
        a = config(ads_base_url='https://ads.example.com/api/internal/v1/', gold_uri='s3://bucket/root/')
        self.assertEqual(a.ads_base_url, 'https://ads.example.com/api/internal/v1')
        self.assertEqual(a.gold_uri, 's3://bucket/root')
        self.assertEqual(a.identity, replace(a, ads_key='ROTATED_KEY').identity)
        self.assertNotIn('NEVER_STORE_THIS_KEY', repr(a))
        self.assertNotIn('NEVER_STORE_THIS_KEY', str(a.identity))
        self.assertEqual(a.state_dir, ROOT / 'artifacts')

    def test_source_binding_changes(self):
        a = config()
        self.assertNotEqual(a.identity, replace(a, ads_base_url='https://other.example.com/api/internal/v1').identity)
        self.assertNotEqual(a.identity, replace(a, gold_uri='s3://other/root').identity)
        self.assertNotEqual(a.identity, replace(a, storage_options={'AWS_ENDPOINT_URL': 'http://storage.internal:9000'}).identity)

    def test_invalid_configuration(self):
        for changes in ({'ads_base_url': 'http://ads.example.com/api/internal/v1'},
                        {'ads_base_url': 'https://user:secret@ads.example.com/api/internal/v1'},
                        {'ads_base_url': 'https://ads.example.com/api/internal/v1?key=secret'},
                        {'ads_key': ''}, {'ads_key': 'x\ny'}, {'gold_uri': ''},
                        {'gold_uri': 's3://bucket/root?secret=x'}, {'http_timeout': float('inf')},
                        {'train_epochs': 0}, {'train_epochs': True}, {'storage_options': {'key': 1}}, {'storage_options': {'AWS_ENDPOINT_URL': ''}},
                        {'storage_options': {'AWS_ENDPOINT_URL': 'https://user:secret@storage.internal'}}):
            with self.subTest(changes=changes), self.assertRaises(JobError):
                config(**changes)

    def test_environment_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            file = Path(directory) / '.env'
            file.write_text('ADS_INTERNAL_BASE_URL=https://ads.example.com/api/internal/v1\nADS_INTERNAL_KEY=FILEKEY\nGOLD_DELTA_URI=s3://bucket/root\nS3_STORAGE_OPTIONS={"AWS_ENDPOINT_URL":"http://storage.internal:9000"}\n')
            os.environ['ADS_INTERNAL_KEY'] = 'ENVKEY'
            actual = Settings.from_env(file)
            self.assertEqual(actual.ads_key, 'ENVKEY')
            self.assertEqual(actual.identity['gold_storage_endpoint'], 'http://storage.internal:9000')
            self.assertEqual(actual.storage_options['AWS_ENDPOINT_URL'], actual.identity['gold_storage_endpoint'])

    def test_endpoint_has_one_source_for_direct_and_environment_construction(self):
        direct = config(storage_options={'AWS_ENDPOINT_URL': 'http://storage-a:9000/'})
        self.assertEqual(direct.identity['gold_storage_endpoint'], 'http://storage-a:9000')
        self.assertEqual(direct.storage_options['AWS_ENDPOINT_URL'], direct.identity['gold_storage_endpoint'])
        with patch.dict(os.environ, {'AWS_ENDPOINT_URL': 'http://storage-b:9000/'}, clear=True):
            configured = config()
        self.assertEqual(configured.identity['gold_storage_endpoint'], 'http://storage-b:9000')
        self.assertEqual(configured.storage_options['AWS_ENDPOINT_URL'], configured.identity['gold_storage_endpoint'])
        with patch.dict(os.environ, {'AWS_ENDPOINT_URL': 'http://storage-c:9000'}, clear=True):
            self.assertEqual(configured.storage_options['AWS_ENDPOINT_URL'], 'http://storage-b:9000')

    def test_bad_json_does_not_echo_secret(self):
        with patch.dict(os.environ, {'S3_STORAGE_OPTIONS': 'SECRET_NOT_JSON'}, clear=True), self.assertRaises(JobError) as caught:
            Settings.from_env('/nonexistent')
        self.assertNotIn('SECRET', str(caught.exception))
