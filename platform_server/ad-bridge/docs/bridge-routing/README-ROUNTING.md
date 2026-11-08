# ad-bridge 라우팅 목록

`ad-bridge/` 안의 구현·테스트·설정 파일만 정리한다. 소스 파일 하나당 같은 상대 경로에 `.md`를 붙인 문서 하나가 대응한다.

현재 게임 실행 코드의 클릭 저장·확인 경로와 연결 상태는 [클릭 처리 라우팅](../CLICK_CONFIRMATION.md)을 참고한다. 아래 목록은 기존 ad-bridge 원본이다.

## 구현

| 소스 파일 | 기능 | 상세 문서 |
| --- | --- | --- |
| [scripts/__init__.py](../../scripts/__init__.py) | 광고 클라이언트·설정·예외·응답 타입 공개 | [문서](scripts/__init__.py.md) |
| [scripts/api/__init__.py](../../scripts/api/__init__.py) | 광고 서버의 HTTP 계약과 연결 설정. | [문서](scripts/api/__init__.py.md) |
| [scripts/api/client.py](../../scripts/api/client.py) | 광고 결정 POST·노출 PUT·판정 GET | [문서](scripts/api/client.py.md) |
| [scripts/api/config.py](../../scripts/api/config.py) | 광고 연결 환경변수 검증 | [문서](scripts/api/config.py.md) |
| [scripts/api/errors.py](../../scripts/api/errors.py) | HTTP·연결·응답 오류 구분 | [문서](scripts/api/errors.py.md) |
| [scripts/api/types.py](../../scripts/api/types.py) | 광고 소재·결정·반응 응답 데이터 | [문서](scripts/api/types.py.md) |
| [scripts/apps.py](../../scripts/apps.py) | Django 광고 앱 등록 | [문서](scripts/apps.py.md) |
| [scripts/decisions/__init__.py](../../scripts/decisions/__init__.py) | 광고 요청과 발급 소유권 기록. | [문서](scripts/decisions/__init__.py.md) |
| [scripts/decisions/service.py](../../scripts/decisions/service.py) | 광고 발급 후 소유권 저장 | [문서](scripts/decisions/service.py.md) |
| [scripts/decisions/store.py](../../scripts/decisions/store.py) | 개인 광고 발급 기록과 소유권 보관 | [문서](scripts/decisions/store.py.md) |
| [scripts/delivery/__init__.py](../../scripts/delivery/__init__.py) | 저장된 노출의 전송과 재전송. | [문서](scripts/delivery/__init__.py.md) |
| [scripts/delivery/policy.py](../../scripts/delivery/policy.py) | 재시도 간격과 임대 시간 계산 | [문서](scripts/delivery/policy.py.md) |
| [scripts/delivery/sender.py](../../scripts/delivery/sender.py) | 노출 한 번 전송 후 상태·재시도 일정 저장 | [문서](scripts/delivery/sender.py.md) |
| [scripts/delivery/worker.py](../../scripts/delivery/worker.py) | 기한이 된 노출을 배치 재전송 | [문서](scripts/delivery/worker.py.md) |
| [scripts/django_urls.py](../../scripts/django_urls.py) | 기존 URL include 경로 유지 | [문서](scripts/django_urls.py.md) |
| [scripts/game/__init__.py](../../scripts/game/__init__.py) | 게임 서버의 Django 요청 연결. | [문서](scripts/game/__init__.py.md) |
| [scripts/game/urls.py](../../scripts/game/urls.py) | 결정·노출·결과 조회 URL 연결 | [문서](scripts/game/urls.py.md) |
| [scripts/game/views.py](../../scripts/game/views.py) | 로그인·소유권 검증과 광고 HTTP 응답 | [문서](scripts/game/views.py.md) |
| [scripts/impressions/__init__.py](../../scripts/impressions/__init__.py) | 개인 슬롯의 노출 접수와 저장. | [문서](scripts/impressions/__init__.py.md) |
| [scripts/impressions/service.py](../../scripts/impressions/service.py) | 최초 노출 예약 후 한 번 전송 | [문서](scripts/impressions/service.py.md) |
| [scripts/impressions/store.py](../../scripts/impressions/store.py) | 노출 예약·소유권·임대·판정 저장 | [문서](scripts/impressions/store.py.md) |
| [scripts/management/__init__.py](../../scripts/management/__init__.py) | 게임 서버에서 실행하는 광고 연동 관리 명령. | [문서](scripts/management/__init__.py.md) |
| [scripts/management/commands/__init__.py](../../scripts/management/commands/__init__.py) | Django가 광고 관리 명령을 찾는 패키지. | [문서](scripts/management/commands/__init__.py.md) |
| [scripts/management/commands/dispatch_ad_reactions.py](../../scripts/management/commands/dispatch_ad_reactions.py) | 노출 자동 재전송·지정 401 복구 명령 | [문서](scripts/management/commands/dispatch_ad_reactions.py.md) |
| [scripts/migrations/0001_initial.py](../../scripts/migrations/0001_initial.py) | 광고 발급·노출 테이블 생성 | [문서](scripts/migrations/0001_initial.py.md) |
| [scripts/migrations/0002_impression_retry.py](../../scripts/migrations/0002_impression_retry.py) | 재전송 필드·인덱스 추가와 기존 대기 기록 복구 | [문서](scripts/migrations/0002_impression_retry.py.md) |
| [scripts/migrations/0003_decision_owner.py](../../scripts/migrations/0003_decision_owner.py) | 발급 기록에 내부 소유자 UUID 추가 | [문서](scripts/migrations/0003_decision_owner.py.md) |
| [scripts/migrations/__init__.py](../../scripts/migrations/__init__.py) | 실제 게임에서 migrate로 적용하는 브리지 저장소 스키마. | [문서](scripts/migrations/__init__.py.md) |
| [scripts/models.py](../../scripts/models.py) | 개인 광고 발급·최초 노출 DB 모델 | [문서](scripts/models.py.md) |
| [scripts/results/__init__.py](../../scripts/results/__init__.py) | 접수된 개인 노출의 광고 서버 판정 조회. | [문서](scripts/results/__init__.py.md) |
| [scripts/results/service.py](../../scripts/results/service.py) | 접수된 노출 판정 조회·대조·저장 | [문서](scripts/results/service.py.md) |
| [scripts/static/ad_bridge/display.js](../../scripts/static/ad_bridge/display.js) | Canvas 광고 표시·클릭 주소 이동 | [문서](scripts/static/ad_bridge/display.js.md) |
| [scripts/static/ad_bridge/impression.js](../../scripts/static/ad_bridge/impression.js) | 화면 내 광고 영역·최초 유효 관측 확인 | [문서](scripts/static/ad_bridge/impression.js.md) |

## 테스트

| 소스 파일 | 기능 | 상세 문서 |
| --- | --- | --- |
| [test/__init__.py](../../test/__init__.py) | 광고 서버·DB 없이 실행하는 브리지 단위 테스트. | [문서](test/__init__.py.md) |
| [test/api/__init__.py](../../test/api/__init__.py) | 테스트 패키지 초기화 | [문서](test/api/__init__.py.md) |
| [test/api/test_client.py](../../test/api/test_client.py) | 외부 서버 없이 요청 형식·오류·키 보호·계약 응답 처리를 확인한다. | [문서](test/api/test_client.py.md) |
| [test/api/test_config.py](../../test/api/test_config.py) | 외부 연결 없이 광고 연결 설정의 입력 경계와 비밀 보호를 검사한다. | [문서](test/api/test_config.py.md) |
| [test/api/test_impression.py](../../test/api/test_impression.py) | 실제 광고 서버 없이 노출 PUT 한 번의 계약·멱등 값·오류 처리를 확인한다. | [문서](test/api/test_impression.py.md) |
| [test/decisions/__init__.py](../../test/decisions/__init__.py) | 테스트 패키지 초기화 | [문서](test/decisions/__init__.py.md) |
| [test/decisions/test_service.py](../../test/decisions/test_service.py) | 광고 결정 요청과 소유권 저장의 순서를 외부 연결 없이 검사한다. | [문서](test/decisions/test_service.py.md) |
| [test/delivery/__init__.py](../../test/delivery/__init__.py) | 테스트 패키지 초기화 | [문서](test/delivery/__init__.py.md) |
| [test/delivery/test_retry.py](../../test/delivery/test_retry.py) | 고정 시계와 메모리 저장소로 노출 재전송 정책을 검사한다. | [문서](test/delivery/test_retry.py.md) |
| [test/delivery/test_retry_command.py](../../test/delivery/test_retry_command.py) | 재전송 명령의 반복·종료·실패 처리를 HTTP·DB·대기 없이 확인한다. | [문서](test/delivery/test_retry_command.py.md) |
| [test/game/__init__.py](../../test/game/__init__.py) | 테스트 패키지 초기화 | [문서](test/game/__init__.py.md) |
| [test/game/test_game_adapter.py](../../test/game/test_game_adapter.py) | DB나 광고 서버 없이 실제 Django 요청 경계와 브리지 호출을 검사한다. | [문서](test/game/test_game_adapter.py.md) |
| [test/game/test_game_impression.py](../../test/game/test_game_impression.py) | 게임 요청부터 실제 노출 서비스까지 검사하고 DB·HTTP 경계만 대체한다. | [문서](test/game/test_game_impression.py.md) |
| [test/impressions/__init__.py](../../test/impressions/__init__.py) | 테스트 패키지 초기화 | [문서](test/impressions/__init__.py.md) |
| [test/impressions/test_impression_service.py](../../test/impressions/test_impression_service.py) | 노출 예약·최초 전송·접수 상태 저장을 외부 연결 없이 검사한다. | [문서](test/impressions/test_impression_service.py.md) |
| [test/integration_tests/__init__.py](../../test/integration_tests/__init__.py) | ads-server의 별도 테스트 DB와 실제 HTTP 서버로 실행하는 선택적 통합 테스트. | [문서](test/integration_tests/__init__.py.md) |
| [test/integration_tests/test_provider.py](../../test/integration_tests/test_provider.py) | ads-server manage.py에서 실행한다. 개발 DB의 광고·계정·매체 키는 사용하지 않는다. | [문서](test/integration_tests/test_provider.py.md) |
| [test/results/__init__.py](../../test/results/__init__.py) | 테스트 패키지 초기화 | [문서](test/results/__init__.py.md) |
| [test/results/test_client.py](../../test/results/test_client.py) | GET 계약은 가짜 HTTP 응답으로 검사한다. | [문서](test/results/test_client.py.md) |
| [test/results/test_service.py](../../test/results/test_service.py) | 조회 서비스의 소유권·실패·저장 순서를 격리해 검사한다. | [문서](test/results/test_service.py.md) |
| [test/run.py](../../test/run.py) | 기능 그룹·단일 사례 테스트 실행 | [문서](test/run.py.md) |
| [test/storage/__init__.py](../../test/storage/__init__.py) | 테스트 패키지 초기화 | [문서](test/storage/__init__.py.md) |
| [test/storage/test_impression_storage.py](../../test/storage/test_impression_storage.py) | 메모리 SQLite로 검사하는 저장소 통합 테스트. 단위 테스트 묶음과 분리한다. | [문서](test/storage/test_impression_storage.py.md) |
| [test/storage/test_result_storage.py](../../test/storage/test_result_storage.py) | 임시 SQLite에서 판정 갱신과 전송 기록 보존을 확인한다. | [문서](test/storage/test_result_storage.py.md) |
| [test/storage/test_retry_storage.py](../../test/storage/test_retry_storage.py) | 재전송 저장소 통합 검사: 메모리 SQLite에서 예약·임대·마이그레이션을 확인한다. | [문서](test/storage/test_retry_storage.py.md) |
| [test/support/__init__.py](../../test/support/__init__.py) | 테스트 패키지 초기화 | [문서](test/support/__init__.py.md) |
| [test/support/clock.py](../../test/support/clock.py) | 실제 대기 없는 테스트 시계 | [문서](test/support/clock.py.md) |
| [test/support/django_settings.py](../../test/support/django_settings.py) | DB 없는 게임 어댑터 테스트 설정 | [문서](test/support/django_settings.py.md) |
| [test/support/django_storage_settings.py](../../test/support/django_storage_settings.py) | 메모리 SQLite 저장소 테스트 설정 | [문서](test/support/django_storage_settings.py.md) |
| [test/support/impression_fakes.py](../../test/support/impression_fakes.py) | 메모리 노출 저장소와 반응 fixture | [문서](test/support/impression_fakes.py.md) |

## 설정

| 소스 파일 | 기능 | 상세 문서 |
| --- | --- | --- |
| [.gitignore](../../.gitignore) | 환경 파일·캐시·로컬 생성물 Git 제외 | [문서](.gitignore.md) |
