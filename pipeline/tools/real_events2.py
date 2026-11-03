"""lab DB에 실제 업무 흐름으로 이벤트를 만든다(광고주·매체 시드 → 결정 → 반응 → 처리). 이름이 겹치지 않게 접미사를 붙인다.
사용: real_events2.py <MEDIA_ROOT> <STATIC_ROOT> <접미사>"""
import os, sys, uuid
from pathlib import Path
from random import Random
BASE = Path(__file__).resolve().parents[2] / "ads_server"
MEDIA, STATIC = (str(Path(x).resolve()) for x in sys.argv[1:3])  # 상대 경로는 실행한 폴더 기준(아래 chdir 전에 고정)
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / "tests" / "tools"))
os.chdir(BASE)
os.environ.update(DJANGO_SETTINGS_MODULE="config.settings.dev", DB_HOST="127.0.0.1", DB_PORT=os.environ.get("PG_PORT", "5010"), DB_USER="postgres",
                  DB_PASSWORD=Path("/opt/postgresql/config/postgres-password").read_text().strip(), DB_NAME="ads_pipeline_lab",
                  MEDIA_ROOT=MEDIA, STATIC_ROOT=STATIC)
import django
django.setup()
from datetime import UTC, datetime
import load_test
from records import api as records
from events.models import EventOutbox
before = EventOutbox.objects.count()
import foundation.api as fa, masters.api as ma
SUF = sys.argv[3]
_su = fa.User.objects.create_superuser; fa.User.objects.create_superuser = lambda u, *a, **k: _su(f"{u}_{SUF}", *a, **k)
_adv = fa.create_advertiser; fa.create_advertiser = lambda u, *a, **k: _adv(f"{u}_{SUF}", *a, **k)
_cm = ma.create_media; ma.create_media = lambda admin, name, *a, **k: _cm(admin, f"{name} {SUF}", *a, **k)
ctx = load_test.seed(advertisers=2)
now = datetime.now(UTC)
for i in range(3):
    d = records.decide(ctx["media_id"], ctx["slot_id"], subject_id=str(uuid.uuid4()), rng=Random(i))
    for rtype in ("IMPRESSION", "IMPRESSION", "CONVERSION"):
        records.receive_reaction(ctx["media_id"], d.decision_id, uuid.uuid4(), rtype, now, {"type": rtype})
shared = records.decide(ctx["media_id"], ctx["shared_slot_id"], rng=Random(9))  # 일괄 슬롯(사람 없음)
records.receive_reaction(ctx["media_id"], shared.decision_id, uuid.uuid4(), "IMPRESSION", now, {"type": "IMPRESSION"}, subject_id=str(uuid.uuid4()))
print("process", records.process_pending())
from collections import Counter
print("new outbox rows", EventOutbox.objects.count() - before, dict(Counter(EventOutbox.objects.values_list("event_name", flat=True))))
