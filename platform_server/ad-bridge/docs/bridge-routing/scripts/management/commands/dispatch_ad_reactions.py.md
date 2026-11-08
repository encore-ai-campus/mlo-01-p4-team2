# scripts/management/commands/dispatch_ad_reactions.py

- 경로: [ad-bridge/scripts/management/commands/dispatch_ad_reactions.py](../../../../../scripts/management/commands/dispatch_ad_reactions.py)
- 기능: 노출 자동 재전송·지정 401 복구 명령
- 호출·연결: Django manage.py → Command.handle → dispatch_due_impressions 또는 _recover_401 → send_once
- 파라미터: --once: 한 배치 실행; --batch-size: 최대 건수(100); --poll-interval: 배치 간 대기 초(1); --recover-401: 복구할 반응 UUID. parser: CLI 파서; options: 해석한 옵션; signum / frame: 종료 신호 인자.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `Command(BaseCommand)` | 데이터·설정·예외 클래스; 필드: help, requires_system_checks |
| `Command.add_arguments(self, parser)` | 명령줄 옵션 등록 |
| `Command.handle(self, *args, **options)` | 옵션 검증 후 재전송 루프 또는 지정 401 복구 실행 |
| `Command.handle.request_stop(signum, frame)` | SIGINT 종료 요청 처리 |
| `Command._recover_401(self, *, client, policy, reaction_id)` | 명시한 기록을 한 번만 복구하며 접수가 확인된 경우에만 성공 종료한다. |
