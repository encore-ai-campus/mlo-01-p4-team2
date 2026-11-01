from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from events.models import EventOutbox

CHUNK = 5000


class Command(BaseCommand):
    help = "MongoDB 로 보낸 지 오래된 outbox 행을 지운다(미전송은 지우지 않는다). 기본 보존 일수는 설정 EVENTS_OUTBOX_RETENTION_DAYS."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="이 일수보다 오래전에 보낸 행을 지운다(설정 EVENTS_OUTBOX_RETENTION_DAYS 이상 — 늘릴 수만 있다)")

    def handle(self, *args, **options):
        retention = settings.EVENTS_OUTBOX_RETENTION_DAYS
        days = retention if options["days"] is None else options["days"]
        if days < max(retention, 0):  # 더 짧게 지우면 export_hop_manifests(이 설정으로 비교 불가 창을 정한다)가 정상 정리를 확정 불일치로 본다
            raise CommandError(f"--days 는 설정 EVENTS_OUTBOX_RETENTION_DAYS({retention}) 이상이어야 합니다.")
        cutoff = timezone.now() - timedelta(days=days)
        total = 0
        while True:
            ids = list(EventOutbox.objects.filter(sent_at__isnull=False, sent_at__lt=cutoff).values_list("pk", flat=True)[:CHUNK])
            if not ids:
                break
            total += EventOutbox.objects.filter(pk__in=ids).delete()[0]
        self.stdout.write(f"deleted: {total}")
