"""Environment configuration and non-secret source identity."""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from errors import JobError

ROOT = Path(__file__).resolve().parent


def parsed_url(value, code):
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise JobError(code)
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise JobError(code) from None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise JobError(code)
    return parsed


@dataclass(frozen=True)
class Settings:
    ads_base_url: str
    ads_key: str = field(repr=False)
    gold_uri: str
    state_dir: Path = ROOT / 'artifacts'
    storage_options: dict = field(default_factory=dict, repr=False)
    http_timeout: float = 15.0
    train_epochs: int = 100

    def __post_init__(self):
        base = self.ads_base_url.strip().rstrip('/')
        parsed = parsed_url(base, 'invalid_ads_base_url')
        local = parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')
        if (not parsed.hostname or not (parsed.scheme == 'https' or local)
                or not parsed.path.rstrip('/').endswith('/api/internal/v1')):
            raise JobError('invalid_ads_base_url')
        if (not isinstance(self.ads_key, str) or not self.ads_key
                or any(not 33 <= ord(c) <= 126 for c in self.ads_key)):
            raise JobError('missing_or_invalid_ads_key')
        uri = self.gold_uri.strip().rstrip('/')
        source = parsed_url(uri, 'invalid_gold_uri')
        if source.scheme not in ('', 's3') or (source.scheme == 's3' and not source.netloc):
            raise JobError('invalid_gold_uri')
        if not source.scheme:
            uri = str((ROOT / uri).resolve())
        if (not isinstance(self.storage_options, dict)
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.storage_options.items())):
            raise JobError('invalid_s3_storage_options')
        options = dict(self.storage_options)
        endpoint = options.get('AWS_ENDPOINT_URL', os.environ.get('AWS_ENDPOINT_URL', '')).rstrip('/')
        if 'AWS_ENDPOINT_URL' in options and not endpoint:
            raise JobError('invalid_storage_endpoint')
        if endpoint:
            value = parsed_url(endpoint, 'invalid_storage_endpoint')
            if value.scheme not in ('https', 'http') or not value.hostname:
                raise JobError('invalid_storage_endpoint')
        if type(self.http_timeout) not in (int, float) or not math.isfinite(self.http_timeout) or self.http_timeout <= 0:
            raise JobError('invalid_http_timeout')
        if type(self.train_epochs) is not int or self.train_epochs < 1:
            raise JobError('invalid_train_epochs')
        path = Path(self.state_dir)
        if urlsplit(str(path)).scheme:
            raise JobError('state_dir_must_be_local')
        object.__setattr__(self, 'ads_base_url', base)
        object.__setattr__(self, 'gold_uri', uri)
        object.__setattr__(self, 'state_dir', path if path.is_absolute() else ROOT / path)
        if endpoint:
            options['AWS_ENDPOINT_URL'] = endpoint
        object.__setattr__(self, 'storage_options', options)

    @property
    def identity(self):
        return {'ads_api_base_url': self.ads_base_url, 'gold_delta_uri': self.gold_uri,
                'gold_storage_endpoint': self.storage_options.get('AWS_ENDPOINT_URL', '')}

    @classmethod
    def from_env(cls, env_file=ROOT / '.env'):
        load_dotenv(env_file, override=False)
        try:
            options = json.loads(os.environ.get('S3_STORAGE_OPTIONS', '{}'))
            timeout = float(os.environ.get('HTTP_TIMEOUT', '15'))
            epochs = int(os.environ.get('TRAIN_EPOCHS', '100'))
        except (ValueError, TypeError):
            raise JobError('invalid_environment_setting_format') from None
        if not isinstance(options, dict):
            raise JobError('invalid_s3_storage_options')
        return cls(os.environ.get('ADS_INTERNAL_BASE_URL', ''), os.environ.get('ADS_INTERNAL_KEY', ''),
                   os.environ.get('GOLD_DELTA_URI', ''), Path(os.environ.get('STATE_DIR', 'artifacts')),
                   options, timeout, epochs)
