"""Write the four-cell SQL comparison, with complete API evidence kept separately."""
from datetime import datetime
import json
from pathlib import Path

ROOT=Path('/private/tmp/platform-query-20261106')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
C=json.loads((ROOT/'comparison.json').read_text())
A=C['variants']['before'];B=C['variants']['after']
E='loadtest/results/20261106-query'
L=['# 100계정 부하 테스트: SQL 튜닝 전후','',
 f'- 작성: {datetime.now().astimezone().isoformat(timespec="seconds")}',
 '- 검토: 독립 검토자 두 명의 승인 대기',
 '- 기준 코드: event `b25ccdd` + 기존 연결 풀·대기열 설정.','',
 '## 1. 네 조건의 결과','',
 '**풀 최대10개·이동 대기열128·이동4+서비스4를 고정했습니다. 비교 변수는 SQL 조회와 트랜잭션 범위입니다.**','',
 '| 쿼리 튜닝 | 부하 | HTTP 요청 | 성공 | 실제 오류 | 미확정 | P95 ms |',
 '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
for name,r in [('전',A),('후',B)]:
 for phase,title in [('casual','일반'),('stress','높음')]:
  v=r[phase]['combined_http_terminal']
  L.append(f"| {name} | {title} | {v['attempts']} | {v['success']} | {v['errors']} | {v['unresolved']} | {v['terminal_ms']['p95']} |")
L.extend(['',
 '**혼합 HTTP는 개선됐습니다. 다만 학습 기능 시험은97/100, 504는3건이므로 전체 성능 통과로 판단하지 않습니다.**',
 '',
 'P95는 요청95%가 끝난 시간입니다. 서로 다른 API의 호출 비중도 전체 P95에 영향을 줍니다.',
 '따라서 아래의 같은 API 비교와 함께 판단해야 합니다. 음수 절감은 지연 증가입니다.','',
 '| 요청 | 일반 전→후 ms | 절감 ms (%) | 높은 부하 전→후 ms | 절감 ms (%) |',
 '| --- | ---: | ---: | ---: | ---: |'])
routes=[('HTTP 전체',None),('메인 상태','GET /api/event-main/status/'),
 ('메인 순위','GET /api/event-main/ranking/'),('업적 조회','GET /api/event-story/achievements/'),
 ('레시피 상태','GET /api/event-story/recipe/status/')]
for title,route in routes:
 ds=[C['aggregate_savings'][phase] if route is None else C['savings'][phase][route] for phase in ('casual','stress')]
 L.append('| '+title+' | '+' | '.join(f"{d['before_p95']}→{d['after_p95']} | {d['saved_ms']} ({d['saved_pct']}%)" for d in ds)+' |')
L.extend(['',
 f'[모든 API·소켓의 요청 수, 응답 코드, P95, 절감 시간·비율]({E}/api-results.md)을 별도로 제공합니다.','',
 '## 2. 무엇을 바꿨는가','',
 '| 같은 완료 계정의 조회 | 튜닝 전 | 튜닝 후 | 응답 데이터 |',
 '| --- | ---: | ---: | --- |',
 '| 메인 상태: 준비 작업 포함 | 56쿼리·쓰기1 | 7쿼리·쓰기0 | SHA-256 일치 |',
 '| 메인 상태: 순수 응답 부분 | 56쿼리·쓰기1 | 4쿼리·쓰기0 | SHA-256 일치 |',
 '| 순위: 25개 목록 | 28쿼리 | 2쿼리 | SHA-256 일치 |','',
 '정의가 정상인 완료 계정의 서비스 함수만 계측했습니다. 인증·중계 쿼리는 이 개수에 포함하지 않았습니다.','',
 '- 이벤트 정의를 묶어서 읽습니다. 정의가 정상이라면 UPDATE를 실행하지 않습니다.',
 '- 정의 보정과 과거 완료 복구를 명시적으로 분리했습니다. 메인 상태 함수는 조회만 합니다.',
 '- 순위 조회는 프로필을 함께 읽고 필요한 목록만 가져옵니다. 계정마다 추가 조회하던 처리를 제거했습니다.',
 '- 메인·레시피·게임·학습·일부 매니저의 응답 조회를 저장 완료 뒤로 옮겼습니다.',
 '- 저장 검증·계정 잠금·순위 잠금·중복 방지는 유지했습니다. 테이블과 마이그레이션은 변경하지 않았습니다.',
 '- 일일 안내는 조회 후 소비합니다. 조회 실패가 일회성 안내를 소모하지 않도록 예외 처리했습니다.',
 '- 저장 이후 응답 조회가 실패하면 저장은 유지됩니다. 재요청해도 최초 시각·접속 UUID·보상을 중복 생성하지 않습니다.',
 '트랜잭션 범위는 [Django 지침](https://docs.djangoproject.com/en/5.2/topics/db/transactions/)을 따릅니다.',
 '프로필 추가 조회는 [select_related](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-related)로 줄였습니다.','',
 '## 3. 업적·완료 요청','',
 '다음은 별도의100계정 동시 기능 시험입니다. 일반·높은 혼합 부하와 합산하지 않았습니다.',
 '성공은 사전에 정한 코드와 결과 필드의 일치입니다. 신규 업적 지급 수와는 다릅니다.',''])
features=[('메인 최초 전달','main:deliver:first'),('메인 전달 재요청','main:deliver:retry'),
 ('비밀 레시피 완료','recipe:finish'),('학습 완료','learning:complete'),
 ('11.11초 타이머 결과','timer:result'),('코드 타이핑 결과','typing:result'),('페어링 정원 결과','term_match:result')]
for title,r in [('튜닝 전',A),('튜닝 후',B)]:
 L.extend(['### '+title,'',
  '| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |',
  '| --- | ---: | ---: | ---: | ---: |'])
 for name,key in features:
  v=r['normal_scenario_requests'][key]
  L.append(f"| {name} | {v['attempts']} | {v['success']} | {v['errors']} | {(v['terminal_ms'] or {}).get('p95','—')} |")
 L.append('')
L.extend(['### 의도한 거절 검증','',
 '| 검증 항목 | 시도 개수 | 예상대로 거절 | 검증 실패 개수 | 반응속도(P95 ms) |',
 '| --- | ---: | ---: | ---: | ---: |'])
for title,r in [('전',A),('후',B)]:
 for key,name in [('typing:over_limit','타이핑 제한 초과'),('ws:guide_talk','먼 거리 꾸왕이 대화'),('main:game_finish:topping','토핑 실패 판정')]:
  v=r['validation_requests'][key]
  L.append(f"| {title}: {name} | {v['attempts']} | {v['success']} | {v['errors']} | {(v['terminal_ms'] or {}).get('p95','—')} |")
L.extend(['',
 '- 제한 초과는400과 정확한 오류 문구를 비교합니다. 먼 거리 대화도 거절 코드와 문구를 비교합니다.',
 '- 토핑 실패는HTTP200과 `success=false`를 비교합니다. 다른400이나 다른 판정은 검증 실패입니다.',
 '- 예상한 거절은 실제 오류가 아닙니다. 정상 요청과 거절 검증의 P95 표본도 분리했습니다.',
 f'- 사전 규칙 소스의 해시를 시험 전에 고정했습니다. [기대 응답 규칙]({E}/expectations.json)과 원본 응답을 보존했습니다.','',
 '## 4. 오류·저장 결과와 지연 원인','',
 '| 확인 항목 | 튜닝 전 | 튜닝 후 |',
 '| --- | ---: | ---: |',
 f"| 반복 접속502 / 시도 | {A['admission']['statuses'].get('502',0)}/1000 | {B['admission']['statuses'].get('502',0)}/1000 |",
 f"| 기능 접속502 / 시도 | {A['functional_ws']['errors_502']}/400 | {B['functional_ws']['errors_502']}/400 |",
 f"| 전체 edge502 | {A['nginx_502']['count']} | {B['nginx_502']['count']} |",
 f"| 기능 시험 실제 오류 / 정상 기대 시도 | {sum(v['errors'] for v in A['normal_scenario_requests'].values())}/{sum(v['attempts'] for v in A['normal_scenario_requests'].values())} | {sum(v['errors'] for v in B['normal_scenario_requests'].values())}/{sum(v['attempts'] for v in B['normal_scenario_requests'].values())} |",
 f"| edge504 | {A['nginx_504']['8096']} | {B['nginx_504']['8096']} |",
 f"| DB 상한 오류 문자열: 로그 줄 | {A['backend_error_lines']['connection_limit']} | {B['backend_error_lines']['connection_limit']} |",
 f"| 풀 시간초과 문자열: 로그 줄 | {A['backend_error_lines']['pool_timeout']} | {B['backend_error_lines']['pool_timeout']} |",
 f"| 종료된 소켓 전송 오류: 로그 줄 | {A['backend_error_lines']['closed_protocol']} | {B['backend_error_lines']['closed_protocol']} |",
 f"| DB 전체 연결 관측 최대 | {A['all_phases_connections']['cluster_max']} | {B['all_phases_connections']['cluster_max']} |",
 f"| 메인 / 레시피 / 학습 완료 계정 | {A['audit']['main_completed']} / {A['audit']['recipe_completed']} / {A['audit']['learning_completed']} | {B['audit']['main_completed']} / {B['audit']['recipe_completed']} / {B['audit']['learning_completed']} |",''])
for title,r in [('전',A),('후',B)]:
 pending=sum(v['pending_at_end'] for phase in ('casual','stress') for v in r[phase]['drain_reports'])
 unresolved=sum(r[p]['combined_http_terminal']['unresolved']+r[p]['chat_terminal']['unresolved']+
  sum(v['unresolved'] for v in r[p]['control_terminal'].values()) for p in ('casual','stress'))
 missing={k:v for k,v in r['scenario_not_reached'].items() if v}
 L.append(f"- {title}: 종료 당시 진행 중 {pending}건을 추적했습니다. 혼합 요청 미확정은 {unresolved}건입니다.")
 L.append(f"- {title}: 앞 단계 실패로 API에 도달하지 못한 계정: "+(', '.join(f'{k} {v}' for k,v in missing.items()) if missing else '없음')+'.')
L.extend([
 '- 튜닝 후 학습 문제 조회2건·정답 제출1건이504였습니다. Nginx는 응답 읽기가 아닌 연결 수립에서5초 시간초과를 기록했습니다.',
 '- 서비스 프런트 대기열50과 연결 제한5초는 고정했습니다. 실제 큐 점유는 계측하지 않아 대기열 포화로 단정하지 않습니다.',
 '- 학습 완료 P95는911.39→1990.08ms로 증가했습니다. 완료 API에 도달한 계정 수도100→97명으로 달라졌습니다.',
 '- 각 조건의GET /502 한 건은 높은 부하의 Chrome 준비 구간에서 발생했습니다. 약25.9초이며 혼합 API 표본에는 포함되지 않습니다.',
 '- 이502는 프런트 응답입니다. 원인별 추적 자료가 부족하여 SQL 지연이나 대기열 부족으로 확정하지 않았습니다.',
 '- 높은 부하의 일반 소켓 요청에도 동작 조건 거절이 있었습니다. 사전 성공 기대와 불일치하므로 실제 오류 열에 별도로 표시했습니다.',
 '- 두 조건 모두 이스터에그7종을 검사했습니다. 계정·업적 중복, 순위 오류, 접속 UUID 누락은 없었습니다.',
 '- HTTP 복원 조회와 소켓 재접속을 구분했습니다. 상세 성공 수는 API 표와 원본 단계 검사에 있습니다.',
 '- 정상 상태 조회의 반복 쓰기와 프로필 추가 조회를 제거했습니다. 저장 잠금 중 응답 조회도 줄였습니다.',
 '- 이 변경은 DB 작업과 잠금 유지 시간을 줄일 수 있습니다. 쿼리별 소요 시간은 계측하지 않았습니다.',
 f"- 전 구간의 풀 대기 관측 최대는 {max(v['waiting_max'] for v in A['pool_stats'].values())}→{max(v['waiting_max'] for v in B['pool_stats'].values())}개입니다.",
 f"- 높은 부하의 생성기 지연 P95는 {A['stress']['reference_movement_ms']['generator_event_loop_lag']['p95']}→{B['stress']['reference_movement_ms']['generator_event_loop_lag']['p95']}ms입니다.",
 '- 서버와 생성기가 CPU를 공유했습니다. 학습 요청·공통 행 잠금·풀 대기가 남아 전체 지연이 사라지지는 않습니다.',
 '- 코드상 가능한 원인과 측정 결과를 구분했습니다. 원인별 지연 비율이나 모든502의 원인을 확정하지 않습니다.',
 '- 자원은 약2초, 풀은 약1초 간격으로 관측했습니다. 로그 줄 수는 실패 요청 수가 아닙니다.','',
 '## 5. 재현 조건·집계 기준','',
 '- 이동4·서비스4·프런트2·jobs1입니다. 풀 최소1·최대10, 앱과jobs의 이론상한은90개입니다.',
 '- 이동 프런트9581 대기열128, 서비스9582 대기열50, OS 상한128을 두 조건에서 유지했습니다.',
 '- PostgreSQL17.7 연결 상한100·shared_buffers32MB와 Redis7.0.15를 사용했습니다. M1 Pro·CPU8·RAM16GB입니다.',
 '- 같은 템플릿에서100계정·문제3,600개·선행 진척을 복제했습니다. Redis는 조건마다 초기화했습니다.',
 '- 세션 키와 접속 UUID는 같습니다. 각 조건 시작 전에 로그인·활동 시각만 갱신했습니다.',
 '- 일반·높은 혼합 부하는 각각180초입니다. 접속 증가15초·준비15초는 집계에서 제외했습니다.',
 '- 혼합 부하는 프로토콜99계정과 독립 headless Chrome1계정입니다. 기능 검증은 프로토콜100계정입니다.',
 f"- 높은 부하의 실제 생성량은 계정당 {A['stress']['generator_summary']['actions_per_user_s']}→{B['stress']['generator_summary']['actions_per_user_s']}행동/초입니다. 목표는10행동/초입니다.",
 '- 메인 선행8단계는 입력한 상태입니다. 토핑 실패 뒤 선행 완료값으로 포장·전달을 검사했습니다.',
 '- 자연스러운 메인 전 과정100명 성공 검사는 아닙니다. 학습은10문제와 동일 UUID 재요청을 시도했습니다.',
 '- 계정별 학습 과목3개·문제 유형2개를 분산했습니다. 레시피의 두 분기를 검사했습니다.',
 '- 기간 전 검증용 `main.window=open`, DEBUG0, 광고0, 세션 자동 청소0입니다. 시스템·게임 시계는 유지했습니다.',
 '- 요청 수는 호출 시도입니다. 최초 전달과 재요청은 분리했고, 미도달 계정은 오류 분모에 넣지 않았습니다.',
 '- P95는 호출 시작부터 본문 수신·오류·시간초과 확정까지입니다. 실행 대기도 포함합니다.',
 '- 확정 표본을 정렬한 `ceil(0.95×N)`번째 값입니다. 미확정에는 임의 시간을 넣지 않았습니다.',
 '- 종료 시 신규 요청을 막고 기존 요청을 최대65초 추적했습니다. 수신과ping은 계속 유지했습니다.',
 '- 혼합 HTTP 호출 제한45초·네트워크30초·WS10초입니다. 기능 HTTP 네트워크·WS 제한은40초입니다.','',
 '## 6. 검증 근거와 한계','',
 f'[전체 API 표]({E}/api-results.md) · [집계]({E}/comparison.json) · [초기 조건]({E}/initial.json) · [코드 변경]({E}/application-change.patch)',
 f'[관련 검사153개 통과]({E}/focused-tests.log) · [집계 검사8개 통과]({E}/accounting-tests.log) · [검토 기록]({E}/reviews.json)',
 f'재사용 테스트DB에서 초기 데이터 누락8건이 있었습니다. 테스트 초기화를 수정한 뒤153개가 통과했습니다. [중간 실패 근거]({E}/fixture-reuse-audit.json)를 보존했습니다.',
 '기존8004 서버와 사용자 DB55474는 변경하지 않았습니다. 원본·로그 전체·해시를 보존했고 세션과 비밀 키는 제외했습니다.',
 '[직전 대기열 비교](loadtest/results/20261106-backlog/report.md)와 [연결 풀 비교](loadtest/results/20261106-local44-pool/report.md)를 보존했습니다.',
 '소스 차이가 있던 예비 실행과 권한 확인용 실행은 제외했습니다. 최종 전후는 동일 계측기와 기대 규칙을 사용했습니다.',
 '두 조건의 Chrome에서 같은 정적파일2종이404였습니다. 마지막 한입 대화 스크립트와 잔량 업적 아이콘이며, 전체 화면 정상 판정은 보류합니다.',
 '전→후를 한 번씩 순서대로 실행했습니다. 장비 변동과 호출 비중 차이가 남아 있어 반복 시험의 통계적 우열을 확정하지 않습니다.',
 '종료된 소켓 전송 오류는 남아 있습니다. 실제 운영망·100개 Chrome·장시간·광고·매니저·장애 복구는 이번에 재시험하지 않았습니다.',
 '이전 전체 회귀747개의 실패25·오류29·생략7을 이번 관련 검사 통과로 대체하지 않습니다.','',
 '**프런트 통합은 검토만 했습니다. 구현·라우팅 변경·통합 성능 시험은 하지 않았습니다.**',
 '중계 제거는 연결·복사를 줄일 수 있지만 개선 폭은 미측정입니다. HTML 통합은 화면과API의 서비스 자원을 공유합니다.',
 '인증·CSRF·WS Origin·Cookie·프록시 헤더 검증과 사설 백엔드 바인딩은 유지해야 합니다.',
 '[Django 보안 지침](https://docs.djangoproject.com/en/5.2/topics/security/)과 [Channels 보안 지침](https://channels.readthedocs.io/en/stable/topics/security.html)을 기준으로 검토했습니다.','',
 '아래 명령은 서버 요청 없이 분모·기대 응답·P95·고정 조건·파일 해시를 검산합니다.','',
 '```sh',f'python3 {E}/harness/verify_compare.py {E}/','```','',
 '보고서 승인은 운영 성능 통과를 뜻하지 않습니다.'])
(REPO/'stress-test.md').write_text('\n'.join(L)+'\n')
print('SQL comparison report written:',len(L),'lines')
