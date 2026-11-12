# WebSocket 종료 정리 검토·수정 보고서

이동·채팅 소켓의 전송 단절 후 정리 누락을 수정해 `event` 작업본에 반영했습니다.
격리 실제 서버 13사례와 오프라인 32사례는 모두 정리됐습니다. 선택한 기존 회귀 시험에는 실패와 오류가 남았습니다.

## 1. 소스로 확인한 원인

- 이동 초기 `welcome`·`pepero_status`와 채팅 초기 `chat_ready`는 접속 등록이나 그룹 가입 후 전송합니다.
- 초기 전송과 활성 연결의 `pong` 전송에서 `Disconnected`가 발생하면, 기존 연결 실행이 끝나 `disconnect`를 건너뛰었습니다.
- 설치된 Channels는 `StopConsumer`만 처리합니다. 연결 실행이 끝나면 뒤의 `websocket_disconnect`를 처리하지 못합니다.
- 이미 실패한 감시 작업이나 그룹 제거의 예외도 나머지 정리를 막았습니다.
- 제거 범위는 해당 연결의 메모리 접속자·소켓·세션 등록과 그룹 구성원 기록입니다. 계정 DB 행을 삭제하지 않습니다.

재현 근거: [이동 결과](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-20261107/results.json), [채팅 결과](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/phase1-chat.json).
수정 전 소스: [이동 등록·전송](/private/tmp/platform-ws-cleanup-fix-20261107/source-before/backend/campus/consumers.py:353), [채팅 가입·전송](/private/tmp/platform-ws-cleanup-fix-20261107/source-before/backend/campus/chat.py:46).

## 2. 반영한 변경

| 최종 변경 파일 | 역할 |
|---|---|
| [socket_lifecycle.py](/Users/sindong-yeob/projects/platform-server/playdatametaverse/backend/campus/socket_lifecycle.py:13) | 연결 실행 외곽 정리와 전송 단절 감지 |
| [consumers.py](/Users/sindong-yeob/projects/platform-server/playdatametaverse/backend/campus/consumers.py:1265) | 이동 작업·위치 저장·접속 등록 정리 |
| [chat.py](/Users/sindong-yeob/projects/platform-server/playdatametaverse/backend/campus/chat.py:229) | 채팅 감시 작업과 그룹 정리 |
| [test_socket_cleanup.py](/Users/sindong-yeob/projects/platform-server/playdatametaverse/backend/campus/test_socket_cleanup.py:185) | 신규 단위 시험 12개 |

- 핸들러 외곽의 `finally`에서 기존 `disconnect`·`leave`를 재사용합니다. 핸들러가 잠금을 놓은 뒤 정리합니다.
- 실제 ASGI `send`의 `OSError`·`Disconnected`만 전송 단절로 처리합니다. 알 수 없는 핸들러·전송 예외는 상위로 전달합니다.
- 배경 `send`도 단절 신호를 보냅니다. 이 신호가 연결 실행을 끝내고 같은 정리를 유도합니다.
- 종료된 작업의 오류와 그룹 제거 오류를 기록한 뒤 나머지 정리를 계속합니다.
- 성공한 그룹만 추적 목록에서 지웁니다. 재정리하면 실패한 그룹을 다시 시도합니다.
- `saving` 작업을 취소 전파에서 보호합니다. 저장 작업을 기다린 뒤 기존 강제 위치 저장을 실행합니다.
- 옛 접속자 ID로 등록을 제거하고 세션의 현재 연결을 확인해 재접속한 새 연결과 다른 계정을 보호합니다.
- 연결 하나를 정리할 때 공유 Redis 계층·DB 풀·클러스터를 닫지 않습니다.

## 3. 시험으로 확인한 결과

실제 서버 시험은 Daphne 한 개(`9801`), PostgreSQL(`55671`), Redis(`57671`·`57672`), 두 계정을 사용했습니다.
표의 정리 완료는 접속 등록·그룹 기록 제거를 뜻합니다. 수정 후에는 시작한 사용자별 작업도 모두 끝났습니다.

| 실제 서버 사례 | 사례 수 | 수정 전 정리 완료 | 수정 후 정리 완료 |
|---|---:|---:|---:|
| 이동 정상·조기 종료, 채팅 정상 종료 | 3 | 3/3 | 3/3 |
| 이동 `welcome`·`pepero_status`·활성 `pong` 전송 단절 | 3 | 0/3 | 3/3 |
| 채팅 `chat_ready`·활성 `pong` 전송 단절 | 2 | 0/2 | 2/2 |
| 이동·채팅 그룹 제거 실패 | 2 | 0/2 | 2/2 |
| 이동·채팅 감시 작업 실패 | 2 | 0/2 | 2/2 |
| 이동 위치 저장 실패 | 1 | 1/1 | 1/1 |
| 합계 | 13 | 4/13 | 13/13 |

수정 전 잔류 9사례는 전송 단절 5사례와 그룹·감시 실패 4사례입니다.
수정 후 모든 사례에서 다른 계정의 `pong` 응답을 유지했습니다.
정상 이동 종료의 최종 위치는 실제 PostgreSQL 값과 일치했습니다.
근거: [수정 전 실제 서버 결과](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/phase2-before-live-v2.json), [수정 후 실제 서버 결과](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/phase2-after-live-v2.json).

전송 단절은 실제 클라이언트를 닫은 뒤 ASGI `send` 콜백에 `Disconnected`를 명시적으로 주입했습니다.
그룹 오류는 첫 제거 시도를 한 번 실패시켰습니다. 감시 오류는 실제 채널 전송을 실패시킨 뒤 클라이언트를 닫았습니다.
0.4초는 연결 실행 종료 확인 후 관찰한 시간입니다. 0.4초 이내 정리됐다는 뜻이 아닙니다.
정상·조기 종료에는 전송 예외를 주입하지 않았습니다. 자연 발생 빈도는 측정하지 않았습니다.

오프라인 시험은 실제 소켓 처리 코드를 사용하고 DB·인증 값과 `send`를 제어했습니다.
실제 DB·네트워크 접속 시도는 0회였습니다. 정리 완료는 6/32에서 32/32로 늘었습니다.
근거: [수정 전 오프라인 결과](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/phase2-before-offline-v3.json), [수정 후 오프라인 결과](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/phase2-after-offline-v3.json).
신규 단위 시험 12개가 통과했습니다. [Head도 event 작업본에서 12개를 별도로 실행해 통과했습니다](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/head-event-tests.json).
단위 시험은 부분 초기화·중복 정리·배경 전송·저장 대기 보호·늦은 옛 연결 정리도 검사했습니다.

## 4. 남은 회귀 시험 실패

선택한 기존 회귀 시험 81개에는 실패와 오류가 남았습니다. 수정 후 초기 광역 실행은 최종 신규 12개 중 11개를 포함했습니다.

| 실행 | 구성 | 통과 | 실패 | 오류 |
|---|---|---:|---:|---:|
| 수정 전 | 기존 81개 | 57 | 17 | 7 |
| 수정 후 초기 광역 | 기존 81개 + 신규 11개 = 92개 | 67 | 18 | 7 |

두 실행에 공통으로 남은 실패·오류는 24개입니다.
수정 후 광역 실행에만 추가된 `slow_position_save`는 단독 실행에서 수정 전·후 모두 실패했습니다.
기준 0.3초에 대해 수정 전 0.3656469초, 수정 후 0.3658078초였습니다.
추가 실패 원인을 동시 실행으로 확정할 근거는 없습니다. 성능 개선이나 선택한 회귀 시험의 전체 통과를 주장할 수 없습니다.
실패 ID와 실행 조건은 [최종 manifest](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/phase2-manifest.json)의 `existing_regressions`에 기록했습니다.

## 5. 한계와 현재 상태

- DB 저장 실패 시험은 오류 기록 후 후속 정리를 확인했습니다. 실패한 위치의 저장을 보장하지 않습니다.
- Redis 장애가 지속되면 제거하지 못한 그룹이 남을 수 있습니다.
- DB 쓰기가 영구 대기하면 `saving`을 기다리는 최종 정리가 늦어질 수 있습니다.
- 프런트 중계·HTTP 502·4+4 작업자 재부하·운영 배포·장기 메모리 영향은 검증하지 않았습니다.
- 과거 예외 16건의 잔류 여부와 실제 발생 빈도는 미확인입니다.

단계① 재현과 단계② 구현·시험은 [Head 승인을 받았습니다](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/review-gates.json).
현재 `event` 작업본의 네 파일 해시는 manifest 및 [반영 기록](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/head-apply.json)과 일치합니다.
기존 `stress-test*.md` 세 파일을 보존했습니다. 공유 실행 서버 재시작·커밋·PR은 없습니다.
시험 전용 Daphne와 PostgreSQL·Redis 컨테이너 세 개는 중지했습니다. [근거와 DB는 보존했습니다](/Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-cleanup-fix-20261107/head-environment-cleanup.json).
이전 보고서는 [보존본](/private/tmp/platform-ws-cleanup-fix-20261107/review-before.md)에 남겼습니다. 이번 갱신은 보고서 한 파일에 한정했습니다.
Head가 이 세션 사용자에게 직접 보고합니다. 외부 연락은 수행하지 않았습니다.
단계③ Head 검토 통과. 수치·근거·소스 일치와 한계 표현을 확인했습니다.
