"""미접수 클릭을 기존 반응 PUT으로 보고하고 저장 확인 한 배치를 실행한다."""
from django.core.management.base import BaseCommand, CommandError
from ad_bridge.click_reporting import configured_report
from ad_bridge.click_worker import check_pending_clicks, configured_lookup


class Command(BaseCommand):
    help = '미확인 클릭을 PUT 보고하고 저장 응답 확인 후 업적을 확정합니다.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--lookup', action='store_true', help='향후 서버 관측 조회 어댑터를 명시적으로 사용')

    def handle(self, *args, **options):
        try:
            callback = {'lookup': configured_lookup()} if options['lookup'] else {'report': configured_report()}
            counts = check_pending_clicks(**callback, limit=options['limit'])
        except (ValueError, ImportError, AttributeError) as error:
            raise CommandError('광고 연결 설정 또는 실행 옵션을 확인해 주세요.') from error
        self.stdout.write(f"처리 {counts['checked']} / 확인 {counts['confirmed']} / 대기 {counts['pending']} / 차단 {counts['blocked']}")
