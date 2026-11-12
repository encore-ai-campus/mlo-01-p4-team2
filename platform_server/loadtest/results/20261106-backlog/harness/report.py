"""Compose the concise report; the complete API tables remain linked evidence."""
from datetime import datetime
import json
from pathlib import Path
ROOT=Path('/private/tmp/platform-backlog-20261106')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
C=json.loads((ROOT/'comparison.json').read_text());A=C['variants']['baseline'];B=C['variants']['increased']
E='loadtest/results/20261106-backlog'
lines=['# 100계정 부하 테스트: 연결 대기열50 →128','',
 f'- 작성: {datetime.now().astimezone().isoformat(timespec="seconds")}',
 '- 검토: 두 독립 검토자 검토 중',
 '- 코드: event `c6290de` + 기존 연결 풀 변경 + 실행기 대기열 변경.','',
 '## 1. 결과','',
 f"반복 접속502는 **{A['admission']['statuses'].get('502',0)}/1000 → {B['admission']['statuses'].get('502',0)}/1000**이었습니다.",
 f"기능 단계 접속502는 **{A['functional_ws']['errors_502']}/400 → {B['functional_ws']['errors_502']}/400**이었습니다.",
 f"HTTP 전체 P95는 일반 **{A['casual']['combined_http_terminal']['terminal_ms']['p95']} → {B['casual']['combined_http_terminal']['terminal_ms']['p95']}ms**, 높은 부하 **{A['stress']['combined_http_terminal']['terminal_ms']['p95']} → {B['stress']['combined_http_terminal']['terminal_ms']['p95']}ms**였습니다.",
 '일반 HTTP와 일부 완료 요청의 P95는 증가했습니다. 소켓 종료 오류도 남아 있어 운영 수용은 보류합니다.',
 '아래는 핵심 요청입니다. [API별 전체 표]('+E+'/api-results.md)에 모든 경로·오류 분류를 실었습니다.',
 '성공은 HTTP 2xx·WS 정상 결과입니다. 게임 승리·신규 업적 지급 수와는 다릅니다.','']
names=[('메인 최초 전달','main:deliver:first'),('메인 전달 재요청','main:deliver:retry'),
 ('비밀 레시피 완료','recipe:finish'),('학습 완료','learning:complete'),
 ('11.11초 타이머 결과','timer:result'),('코드 타이핑 결과','typing:result'),('페어링 정원 결과','term_match:result'),
 ('SQL 기억 게임 결과','main:game_finish:sql_memory'),('꾸왕이 타격 종료','main:game_finish:kkuwang_touch'),
 ('메인 카드 게임 결과','main:game_finish:card_match'),('코팅 결과','main:game_finish:coating'),
 ('토핑 실패 판정','main:game_finish:topping'),('포장 결과','main:game_finish:wrapping'),
 ('타이핑 제한 초과 제출','typing:over_limit')]
for label,r in [('A: 대기열50',A),('B: 대기열128',B)]:
 lines.extend(['### '+label,'','| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |','| --- | ---: | ---: | ---: | ---: |'])
 groups=[('반복 이동 접속',r['admission']['requests']),('기능 단계 이동 접속',r['scenario_requests']['ws_connect'])]
 groups += [(name,r['scenario_requests'][key]) for name,key in names]
 for name,v in groups:
  lines.append(f"| {name} | {v['attempts']} | {v['success']} | {v['errors']} | {(v['terminal_ms'] or {}).get('p95','—')} |")
 lines.append('')
lines.extend([
 '## 2. 조건과 집계 기준','',
 '- 혼합 부하는 프로토콜99계정·실제 headless Chrome1계정입니다. 일반·높은 부하를 각각180초 측정했습니다.',
 '- 접속 증가15초와 준비15초는 제외했습니다. 기능 시험은100계정의 프로토콜 요청으로 진행했습니다.',
 f"- 높은 부하 목표는 계정당10행동/초입니다. 실제 생성량은 A{A['stress']['generator_summary']['actions_per_user_s']}·B{B['stress']['generator_summary']['actions_per_user_s']}행동/초였습니다.",
 '- 반복 접속은100계정×10회입니다. 기능 접속은 세 미니게임 진입과 마지막 재접속, 각각100회입니다.',
 '- 이동4·서비스4·프런트2·jobs1, DB 풀은 최소1·최대10개입니다. 앱·jobs의 이론상한90개에 관리 연결이 추가됩니다.',
 '- 이동 프런트9581의 TCP 대기열만50→128로 바꿨습니다. 서비스9582는50, OS 상한은128로 유지했습니다.',
 '- 실제 커널 대기열을 확인했습니다. 큐 점유나 overflow를 측정한 것은 아닙니다.',
 '- 같은 DB 템플릿에서 계정·세션·문제3,600개·선행 진척을 복사했습니다. Redis는 각 군을 비웠습니다.',
 '- M1 Pro·CPU8·RAM16GB에서 PG17.7·연결 상한100·shared_buffers32MB를 공유했습니다.',
 '- 로컬 운영 구조 재현입니다. 기존8004 서버와 사용자 DB55474는 유지했습니다.',
 '- 기간 전 시험을 위해 전용 서버의 main.window만open으로 고정했습니다. 시스템·게임 시계는 바꾸지 않았습니다.',
 '- DEBUG=0입니다. 광고 연동과 세션 자동 청소는 비활성화했습니다.',
 '- 요청 개수는 API별 호출 시도입니다. 같은 계정의 재요청도 각각 셉니다.',
 '- 오류는 업무 거절·서버 오류·전송 오류·시간초과의 합입니다. 의도한400 거절도 포함합니다.',
 '- P95는 호출 시작부터 본문 수신·오류·시간초과 확정까지입니다. 생성기 실행 대기도 포함합니다.',
 '- 정렬한 확정 표본의 ceil(0.95×N)번째 값입니다. 미확정에는 임의 시간을 넣지 않았습니다.',
 '- 종료 시 신규 요청을 막았습니다. 기존 요청의 수신과 ping을 유지하며 최대65초 추적했습니다.',
 '- 혼합 HTTP 호출 제한45초·네트워크30초·WS10초입니다. 기능 HTTP·WS 제한은40초입니다.',
 ''])
for label,r in [('A',A),('B',B)]:
 pending=sum(v['pending_at_end'] for k in ('casual','stress') for v in r[k]['drain_reports'])
 unresolved=sum(r[k]['combined_http_terminal']['unresolved']+r[k]['chat_terminal']['unresolved']+
  sum(v['unresolved'] for v in r[k]['control_terminal'].values()) for k in ('casual','stress'))
 welcome=r['admission']['welcome_ms']
 missing=', '.join(f"{name} {r['scenario_not_reached'][key]}계정" for name,key in [('타이머','timer:result'),('타이핑','typing:result'),('페어링','term_match:result')] if r['scenario_not_reached'][key]) or '없음'
 lines.extend([f"- {label}: 진행 중 {pending}건을 추적했고 미확정은 {unresolved}건입니다. 접속 성공만의 welcome P95는 {welcome['p95']}ms(N={welcome['n']})입니다.",
  f"- {label}: 결과 API 미도달은 {missing}입니다. 앞 단계에서 실패했으므로 API 오류 분모에 넣지 않았습니다.",''])
lines.extend(['## 3. 오류 원인과 저장 검증',''])
for label,r in [('A',A),('B',B)]:
 reset=r['nginx_502']['by_upstream'].get('127.0.0.1:9581',{})
 values=reset.get('elapsed_ms',[])
 extra=r['nginx_502']['by_upstream'].get('127.0.0.1:9582',{})
 lines.append(f"- {label}: 전체 edge502 {r['nginx_502']['count']}건 중 이동 프런트9581은 {reset.get('count',0)}건입니다."+
  (f" 소요 시간은 {min(values):.0f}~{max(values):.0f}ms입니다." if values else ''))
 if extra:lines.append(f"- {label}: 서비스 프런트9582의 별도502도 {extra['count']}건입니다. 시간(ms): {extra['elapsed_ms']}.")
lines.extend([
 '- 이동 앞단의 Connection reset·Broken pipe를 확인했습니다. 실제 큐 점유·overflow는 측정하지 않았습니다.',
 '- 따라서 대기열 부족이 모든502의 유일 원인이라고 확정하지 않습니다.',
 '- 접속 실패로 게임 API 요청 수가 다릅니다. 완료 요청의 P95에는 이 차이도 영향을 줍니다.',
 f"- DB 전체 연결 관측 최대는 {A['all_phases_connections']['cluster_max']}→{B['all_phases_connections']['cluster_max']}개입니다. 풀 크기는 모두10개 이내였습니다.",
 f"- DB 상한 오류 문자열은 {A['backend_error_lines']['connection_limit']}→{B['backend_error_lines']['connection_limit']}줄, 풀 시간초과는 {A['backend_error_lines']['pool_timeout']}→{B['backend_error_lines']['pool_timeout']}줄입니다.",
 f"- 소켓 종료 후 전송 오류 문자열은 {A['backend_error_lines']['closed_protocol']}→{B['backend_error_lines']['closed_protocol']}줄입니다. 줄 수는 실패 요청 수가 아닙니다.",
 '- 연결 대기열은 순간 접속을 기다리게 합니다. SQL·HTML 처리 능력을 늘리는 설정은 아닙니다.',
 f"- 풀 대기 관측 최대는 {max(v['waiting_max'] for v in A['pool_stats'].values())}→{max(v['waiting_max'] for v in B['pool_stats'].values())}개입니다. 생성기 지연 P95는 {A['stress']['reference_movement_ms']['generator_event_loop_lag']['p95']}→{B['stress']['reference_movement_ms']['generator_event_loop_lag']['p95']}ms입니다.",
 '- 서버와 생성기는 CPU를 공유했습니다. 순위 조회·공통 행 잠금도 코드에 존재합니다.',
 '- 쿼리별 시간 추적은 수행하지 않았습니다. 각 원인이 지연의 몇%인지 확정할 수 없습니다.',
 '- 자원은 약2초·풀은 약1초 간격으로 관측했습니다. 순간 최대를 놓칠 수 있습니다.',
 '- 메인은8개 선행 단계를 입력했습니다. 토핑은 실패 경로이며, 선행 완료값으로 포장·전달을 검사했습니다.',
 '- 자연스러운 메인 전 과정100명 성공 검사는 아닙니다. 학습은 계정당10문제를 풀고 동일 UUID를 재요청했습니다.',
 '- 학습 과목3개·문제 유형2개를 계정별로 분산했습니다. 레시피는 두 분기를 검사했습니다.',
 '- 메인·레시피·학습 완료는 두 군 모두 각각100계정입니다. HTTP 복원 조회도 두 군 모두100/100입니다.',
 '- HTTP 복원 조회는 소켓 재접속 성공과 별개입니다. 소켓 재접속은 A78/100·B100/100입니다.',
 '- 이스터에그7종을 검사했습니다. 두 군 모두 중복 계정·중복 업적·순위·접속 UUID 누락은0건입니다.',
 '- 신규 업적 지급의 작은 표본은 대표 P95로 해석하지 않습니다. API 성공 수와 신규 지급 수를 구분합니다.','',
 '## 4. 프런트 통합 검토: 적용하지 않음','',
 '검토 대상은 Python 프런트 중계 서버입니다. 브라우저의 JS·게임 화면과 Nginx는 유지하는 가정입니다.',
 '현재 HTTP 인증·CSRF와 WS Origin·세션 검사는 백엔드가 담당합니다. 프런트 삭제 자체가 보안 검사를 대신하지 않습니다.','',
 '| 검토안 | 성능 | 필요한 보존·이전 |',
 '| --- | --- | --- |',
 '| WS 중계만 우회 | 중계 연결·메시지 복사를 줄일 가능성. 개선 폭 미측정 | Host·Origin·Cookie·Upgrade 전달, 역할별 경로 제한, 백엔드 사설 바인딩 |',
 '| HTML·API 완전 통합 | 중계는 줄지만 HTML과 API가 서비스 자원을 공유. 개선 폭 미측정 | 템플릿·폼·CSRF·escaping·오류 안내·정적 경로·private/no-store 이전 |','',
 '백엔드는 현재 화면용 JSON을 반환합니다. Nginx 경로만 바꾸면 HTML 화면 통합이 끝나지 않습니다.',
 '신뢰하는 Nginx가 X-Forwarded-Proto를 덮어써야 합니다. 백엔드 포트는 사설망으로 제한해야 합니다.',
 'HTTP CSRF·신뢰 프록시·HTTPS 설정은 [Django 보안 지침](https://docs.djangoproject.com/en/5.2/topics/security/)에 맞춰 유지해야 합니다.',
 'WS 연결의 Origin 제한도 유지해야 합니다. [Channels 보안 지침](https://channels.readthedocs.io/en/stable/topics/security.html)을 확인했습니다.',
 '두 안은 정적 검토입니다. 통합 코드·라우팅 변경·통합 성능 시험은 수행하지 않았습니다.','',
 '## 5. 근거와 한계','',
 f'[전체 API 표]({E}/api-results.md) · [원본 집계]({E}/comparison.json) · [초기 조건]({E}/initial.json) · [코드 변경]({E}/application-change.patch)',
 f'[실행기 검사5개]({E}/launcher-tests.log) · [계측 검사7개]({E}/accounting-tests.log) · [검토 기록]({E}/reviews.json)',
 '군별 backend-logs/*.gz와 nginx-logs/에 전체 로그를 보존했습니다. 비밀 키·세션·근접 토큰은 제외했습니다.',
 '[이전 연결 풀 보고서](loadtest/results/20261106-local44-pool/report.md)와 [기존3+3 보고서](loadtest/results/20261106-local33/report.md)는 보존했습니다.',
 '집계 보완 전 예비 시험과 중단한 부분 실행은 최종 수치에서 제외했습니다.',
 'A 다음 B를1회씩 실행했습니다. 장비 상태·문항·게임 조건 변동이 남아 있어 통계적 우열을 확정하지 않습니다.',
 '실행기의 기본128은 두 프런트 역할에 적용됩니다. 서비스 프런트128 조건의 부하 효과는 이번에 측정하지 않았습니다.',
 '전체 회귀는 재실행하지 않았습니다. 이전747개 결과의 실패25·오류29·생략7은 이번 통과로 바꾸지 않았습니다.',
 '100개 Chrome·장시간 운영·실제 운영망·광고·매니저 경로·DB/Redis 장애는 이번 재시험 범위가 아닙니다.',
 '다음 명령은 서버에 요청하지 않고 API 분모·오류 분류·P95·로그·파일 해시를 검산합니다.','',
 '```sh',f'python3 {E}/harness/verify_compare.py {E}/','```','',
 '보고서 승인은 운영 성능 통과를 뜻하지 않습니다.'])
(REPO/'stress-test.md').write_text('\n'.join(lines)+'\n')
print('Report written:',len(lines),'lines')
