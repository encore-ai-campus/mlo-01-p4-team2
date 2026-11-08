"""Build the requested Markdown report from measured evidence, without credentials."""
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from metrics import OUT

r=json.loads((OUT/'results.json').read_text())
s,c=r['stress'],r['casual']
versions=json.loads((OUT/'versions.json').read_text())
reproduction=Path(__file__).with_name('reproduction.md').read_text()
evidence='loadtest/results/20261105-100-users'
def ms(metric):return f"{metric['p95']:,.2f} ms" if metric else '표본 없음'
def n(metric):return str(metric['n']) if metric else '0'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |',
        *['| '+' | '.join(str(v).replace('\n',' ') for v in row)+' |' for row in rows]])
def phase(name):return next(row for row in r['checks'] if row['phase']==name)
def scenario(label):return r['scenarios'].get(label)
def pretty_checks(row):return f"{row['passed']}/{row['users']}"

metrics=[]
for label,key in [('내 이동 상태 갱신 간격','own_state_interval'),('이동 입력→moving 상태 추정†','input_to_first_moving_state_estimate'),('다른 유저 이동 갱신 간격†','other_player_update_interval')]:
    metrics.append([label,ms(c['reference_movement_ms'][key]),n(c['reference_movement_ms'][key]),ms(s['reference_movement_ms'][key]),n(s['reference_movement_ms'][key])])
metrics.extend([['채팅 전송→같은 메시지 수신',ms(c['chat']['response_ms']),n(c['chat']['response_ms']),ms(s['chat']['response_ms']),n(s['chat']['response_ms'])],
    ['점프 제어→결과 수신',ms(c['controls']['jump']['response_ms']),n(c['controls']['jump']['response_ms']),ms(s['controls']['jump']['response_ms']),n(s['controls']['jump']['response_ms'])],
    ['HTTP 전체',ms(c['http_all_ms']),n(c['http_all_ms']),ms(s['http_all_ms']),n(s['http_all_ms'])]])
for label,path in [('메인 진행 조회','/api/event-main/status/'),('완료 순위 조회','/api/event-main/ranking/'),('업적 조회','/api/event-story/achievements/'),('레시피 보관 상태 조회','/api/event-story/recipe/status/')]:
    metrics.append([label,ms(c['http_ms'][path]),n(c['http_ms'][path]),ms(s['http_ms'][path]),n(s['http_ms'][path])])

outcome_rows=[]
for mode,value in [('일반',c),('높은 부하',s)]:
    for name,record in [('채팅',value['chat']),*value['controls'].items()]:
        counts=record['outcomes']
        outcome_rows.append([mode,name,record['started'],counts.get('ok',0),counts.get('rejected',0),counts.get('timeout',0),counts.get('pending',0),record['no_response_at_end'],record['late_responses']])

feature_rows=[]
for name,label in [('standalone:timer','11.11초 타이머·재전송'),('standalone:typing','타이핑·다시하기·시간 초과 거절'),('standalone:term_match','IT 카드 짝 맞추기·재전송'),('main_games_and_final_rank','메인 단계 API·최종 전달·순위'),('recipe_full_branches','레시피 획득·보류·두 토핑·완료'),('learning_all_subjects_and_types','Python·SQL·CS, 객관식·빈칸'),('state_after_reconnect','재접속 후 DB 상태와 API 비교')]:
    row=phase(name);feature_rows.append([label,pretty_checks(row),f"{row['seconds']:.2f}초",'성공' if row['passed']==100 else '실패 포함'])
for row in r['npc_checks']:
    if row['phase'] in ('manager_greet','guide_talk_one_remaining','manager_poke_with_inventory'):
        feature_rows.append([row['phase'],pretty_checks(row),f"{row['seconds']:.2f}초",'성공' if row['passed']==100 else '실패 포함'])
for row in r.get('private_poke_checks',[]):
    if row['phase']=='poke:eleventh_manager_hit':feature_rows.append([row['phase'],pretty_checks(row),f"{row['seconds']:.2f}초",'성공' if row['passed']==100 else '실패 포함'])
award_rows=[]
for label,value in r['award_requests'].items():
    award_rows.append([label,value['new_awards'],n(value['new_award_ms']),ms(value['new_award_ms']),n(value['no_award_ms']),ms(value['no_award_ms'])])

burst=[]
for label,key in [('게임 시작 제어(타이핑 재시작 포함)','ws:game_start'),('타이머 결과 저장','timer:result'),('타이핑 결과 저장','typing:result'),('IT 카드 결과 저장','term_match:result'),('메인 최종 전달·재전송','main:deliver'),('레시피 획득','recipe:acquire'),('레시피 완료','recipe:finish'),('학습 시작','learning:start'),('학습 정답 제출','learning:answer'),('학습 답 재전송','learning:answer_retry'),('학습 완료','learning:complete')]:
    metric=scenario(key)
    if metric:burst.append([label,metric['n'],f"{metric['p50']:,.2f}",f"{metric['p95']:,.2f}",f"{metric['p99']:,.2f}",metric['unexpected'],json.dumps(metric['statuses'])])
main_rows=[]
for code in ('sql_memory','kkuwang_touch','card_match','coating','topping','wrapping'):
    start,finish=scenario('main:game_start:'+code),scenario('main:game_finish:'+code)
    if start and finish:main_rows.append([code,start['n'],ms(start),finish['n'],ms(finish),'정상 실패 판정 경로' if code=='topping' else '서버 결과 확인'])

all_samples=[json.loads(line) for line in (OUT/'scenario-samples.jsonl').read_text().splitlines()]
http=[row for row in all_samples if isinstance(row['status'],int) and row['status']!=101]
server_errors=[row for row in http if row['status']>=500]
business=[row for row in http if row['status']==409]
errors=sorted({row['action'] for row in server_errors})
resources=[]
for name,value in [('일반',c),('높은 부하',s)]:
    for process,record in value['resources']['processes'].items():
        resources.append([name,process,record['cpu_percent']['p95'],record['cpu_percent']['max'],record['rss_mib_max'],value['resources']['db_connections_max']])
audit=r['audit']
backend_log=(OUT/'backend-regression.log').read_text()
frontend_log=(OUT/'frontend-regression.log').read_text()
query_rows=[[path,row['n'],row['sql_counts'],row['profile_reads'],ms(row['sql_ms'])] for path,row in r['diagnostic_http'].items()]
queue_rows=[[fn,row['n'],ms(row['queue_ms']),ms(row['function_ms']),ms(row['sql_ms'])] for fn,row in r['diagnostic_socket_db'].items() if row['n']>=20]
for label,source,key in [('매니저 인사·업적 판정',r['npc'],'ws:manager_greet'),('잔량 1 대화·업적 판정',r['npc'],'ws:guide_talk'),('매니저 11회 적중·업적 판정',r.get('private_poke',{}),'ws:poke')]:
    metric=source.get(key)
    if metric:burst.append([label,metric['n'],f"{metric['p50']:,.2f}",f"{metric['p95']:,.2f}",f"{metric['p99']:,.2f}",metric['unexpected'],json.dumps(metric['statuses'])])

document=f'''# 100명 동시 사용자 스트레스 테스트 보고서

- 작성일: {datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')}
- 검토 상태: 독립 검토 대기
- 대상 코드: `a747e7980848b4c5b677f5fc132b7dd103e8ce40` (PR #54 작업본)
- 판정: **현재 구성은 요청이 몰릴 때의 처리와 높은 행동 부하를 개선해야 합니다.**
- 근거: [측정 결과 JSON]({evidence}/results.json), [요청별 원본]({evidence}/scenario-samples.jsonl.gz)

## 1. 결론

100명 접속 자체와 여러 기능의 저장은 확인했습니다.
하지만 요청이 몰린 구간에서 HTTP 500 오류가 발생했습니다.
높은 행동 부하에서는 제어 요청이 10초 이상 지연됐습니다.
따라서 운영 환경의 100명 수용을 승인할 근거로 사용할 수 없습니다.

일반 부하의 HTTP P95는 **{ms(c['http_all_ms'])}**였습니다.
높은 부하의 채팅 응답 P95는 **{ms(s['chat']['response_ms'])}**였습니다.
점프 요청 9,590건 중 8,048건, **83.92%**가 10초를 넘겼습니다.
이 P95는 응답을 받은 요청으로 계산했습니다.
시간 초과와 미완료 요청은 별도 표에 포함했습니다.

동시 API 시나리오의 HTTP 응답은 {len(http):,}건입니다.
HTTP 5xx는 {len(server_errors)}건이며, 응답 기준 오류율은 {len(server_errors)/max(1,len(http))*100:.3f}%입니다.
409 응답 {len(business)}건은 앞 단계 미완료에 따른 업무상 거절입니다.
이 비율은 다른 측정 구간과 합산한 전체 서비스 오류율이 아닙니다.

## 2. 환경과 데이터 보호

| 항목 | 실제 측정 조건 |
| --- | --- |
| 장비 | {r['hardware']['cpu']}, 논리 CPU {r['hardware']['logical_cpus']}개, 메모리 16GiB |
| OS / Python | macOS 14.6.1 / Python {r['hardware']['python']} |
| 주요 패키지 | Django {versions['Django']}, Channels {versions['channels']}, Daphne {versions['daphne']}, psycopg {versions['psycopg']} |
| 앱 구조 | 프런트 프록시 1개 → 백엔드 1개 → PostgreSQL |
| 접속 경로 | `127.0.0.1:8055` → `127.0.0.1:9055` |
| DB | PostgreSQL {r['test_db']['server_version']}, 테스트 전용 포트 55479 |
| DB 연결 상한 | 테스트·실제 서버 모두 {r['test_db']['max_connections']} |
| DB shared_buffers | 테스트 {r['test_db']['shared_buffers']}, 실제 서버 {r['live_db']['shared_buffers']} |
| Redis | 사용하지 않음. 단일 프로세스 구성 |
| 운영 시간 | `EVENT_STORY_PREVIEW=1`, 실제 운영 시간 제한을 우회한 기능 측정 |
| 계정 | 서로 다른 로그인 계정 100개, 계정별 접속 세션 |
| 문제은행 | 실제 저장소 문항 3,600개. 18개 분류별 200개 |
| 연결 | 혼합 부하는 이동·채팅 소켓을 계정별로 분리함 |

실제 사용자 DB에는 부하 요청이나 테스트 데이터를 넣지 않았습니다.
실제 DB에서는 연결 상한과 문제 개수만 읽었습니다.
기존 완료 순위와 경품 기록을 삭제하지 않았습니다.
운영 코드와 테이블 구조도 변경하지 않았습니다.

부하 생성기와 서버는 같은 장비에서 실행했습니다.
CPU와 디스크를 공유하므로 전용 서버 결과와 다를 수 있습니다.
테스트 DB 메모리 설정도 실제 서버와 다릅니다.
이 결과를 운영 서버의 정확한 처리 한도로 해석하면 안 됩니다.

## 3. 시나리오와 판정 기준

| 구분 | 사용자 수 | 준비 / 측정 | 행동 |
| --- | --- | --- | --- |
| 일반 부하 | 100명 | 15초 접속 증가 + 15초 준비 + 180초 측정 | 평균 20% 이동, 간헐적 점프·찌르기·채팅·패널 조회 |
| 높은 행동 부하 | 100명 | 동일 | 계정당 초당 약 {s['generator_summary']['actions_per_user_s']}회 행동, 이동 입력 100ms 간격 |
| 동시 기능 요청 | 100명 | 단계별 동시 시작 | 게임 3종, 메인 게임 6종, 레시피, 학습, 결과 재전송 |
| NPC 대화 | 100명 | 근접 위치 사전 배치 | 실제 WebSocket 근접 검사와 대화 제어 |
| 서버 재시작 | 100계정 | HTTP 동시 실행 10개 | DB의 기존 상태와 재시작 후 API 응답 비교 |
| 기능 회귀 | 별도 테스트 DB | 부하 측정과 분리 | 인증·권한·시간 제한·동시성·지급·회수·분기 검사 |

이동·찌르기의 화면 반영 100ms, 대화·게임 시작 300ms, 결과 저장 500ms를 참고 목표로 사용했습니다.
이는 확정된 운영 SLA가 아닙니다.
이번 측정은 서버 응답까지이며 화면 그리기 시간은 포함하지 않습니다.
목표를 넘긴 응답은 개선 대상으로 판단했습니다.
목표 이하의 응답만으로 전체 화면 성능을 통과시키지는 않았습니다.

### 측정 방법

- HTTP: 요청 실행 직전부터 프록시 응답 본문 수신까지 측정했습니다.
- 제어 요청: WebSocket `requestId`별 결과 수신 시간을 측정했습니다.
- 채팅: 보낸 메시지 본문과 같은 메시지가 돌아온 시간을 연결했습니다.
- P95: 값을 정렬한 뒤 `ceil(0.95 × 표본 수)`번째 값을 사용했습니다.
- HTTP 실패 응답도 응답 시간 표본에 포함했습니다.
- 10초를 넘긴 제어 요청은 이후 응답이 와도 시간 초과로 유지했습니다.
- 제어·채팅은 측정 종료 시 미응답을 별도로 기록했습니다.
- 혼합 부하 HTTP는 수신한 응답을 기록했습니다. 종료 시 대기 중인 HTTP 요청은 별도 집계하지 않았습니다.
- 따라서 혼합 부하 HTTP의 완료율과 종료 시 미응답 수는 확정하지 않았습니다.
- 낮은 표본 수의 P95는 참고값입니다. 별도 반복 실험의 신뢰구간은 계산하지 않았습니다.

† 이동 입력 지표는 입력별 식별자가 없는 참고 추정치입니다.
후속 입력이 측정 시작 시각을 덮어쓸 수 있습니다.
다른 유저의 갱신 간격에는 거리별 전송 주기가 포함됩니다.
두 지표를 정확한 입력 왕복 시간으로 해석하면 안 됩니다.

## 4. 일반 부하와 높은 행동 부하의 P95

모든 시간 단위는 ms입니다. N은 응답 또는 상태 수신 표본 수입니다.

{table(['항목','일반 P95','N','높은 부하 P95','N'],metrics)}

일반 부하 HTTP 오류는 수신한 응답 기준 {c['http_unexpected']}건입니다.
높은 부하 HTTP 오류는 수신한 응답 기준 {s['http_unexpected']}건입니다.
측정 중 소켓 종료 집계는 일반 `{c['generator_summary']['closes']}`, 높은 부하 `{s['generator_summary']['closes']}`입니다.
이는 지연이나 미완료 요청이 없다는 의미가 아닙니다.

### 제어 요청과 채팅의 전체 결과

{table(['부하','행동','시작','정상 응답','10초 이내 거절 응답','10초 초과','종료 시 대기','미응답 합계','초과 후 늦은 응답'],outcome_rows)}

정상 응답·10초 이내 거절 응답·10초 초과·종료 시 대기의 합이 시작 건수입니다.
미응답 합계는 끝까지 결과를 받지 못한 건수입니다.
따라서 시간 초과 후 미응답도 포함합니다.
늦은 응답은 시간 초과의 일부이며 다시 합산하지 않습니다.
채팅에는 10초 타임아웃을 적용하지 않았습니다.
미응답을 제외한 P95만으로 성공을 판단하지 않았습니다.

## 5. 100명 동시 접속의 기능 요청 구간

{table(['시나리오','완료 / 시도 계정','전체 실행 시간','결과'],feature_rows)}

전체 실행 시간에는 게임의 대기 시간과 여러 요청이 포함됩니다.
이 값을 개별 요청의 지연 시간으로 사용하지 않았습니다.

### 동시 요청 지연

{table(['행동','N','P50 ms','P95 ms','P99 ms','예상 밖 응답','응답 상태'],burst)}

### 메인 게임별 API 지연

{table(['게임','시작 N','시작 P95','결과 N','결과 P95','검증 경로'],main_rows)}

### 실제 신규 업적 지급과 지급 없는 판정

게임 결과 API는 게임 결과 저장과 업적 판정을 함께 수행합니다.
따라서 위 P95는 신규 업적 지급만의 시간이 아닙니다.
이미 보유했거나 달성이 마감된 계정의 판정도 포함합니다.
HTTP 200과 신규 업적 지급을 구분했습니다.

{table(['요청','신규 지급 수','지급 표본 N','지급 시 P95/단일값','미지급 표본 N','미지급 P95'],award_rows)}

신규 지급 표본이 한 개면 P95와 그 응답 시간이 같습니다.
한 개의 표본으로 신규 지급 성능을 일반화할 수 없습니다.
매니저 찌르기는 별도 업적 알림을 보내므로 해당 응답의 지급 수와 직접 연결하지 않았습니다.
매니저 찌르기 업적은 최종 DB 기록과 기능 회귀로 확인했습니다.

### 사전 준비와 검증 한계

메인 단계의 선행 조건은 테스트 DB에 미리 저장했습니다.
`investigate`는 1단계 완료 기록이 있는 계정의 반복 요청입니다.
`room1_briefing`도 이전 실행의 안내 기록이 남아 있어 재조회 경로입니다.
API 부하를 측정하려고 일부 상호작용 토큰도 테스트 코드에서 서명했습니다.
따라서 이 검사는 사용자의 맵 이동부터 전체 메인 완주까지 재현한 결과가 아닙니다.
실제 토큰 발급과 근접 조건은 별도 기능 회귀에서 검사했습니다.
결과 요청은 각 게임의 시작 응답을 받은 뒤 보냈습니다.
따라서 100명이 정확히 같은 순간 결과를 제출한 실험은 아닙니다.

토핑 게임은 고정 위치 기록을 제출해 정상 실패 판정을 확인했습니다.
토핑 성공 기록은 선행 조건으로 사전 저장했습니다.
토핑 성공 분기 자체는 기능 회귀 테스트에서 검사했습니다.
메인 순위 표는 최종 전달 API의 동시 실행 결과입니다.

학습은 Python·SQL·CS를 분산했습니다.
객관식과 빈칸 문제를 모두 사용했습니다.
정답은 테스트 코드가 문제은행에서 가져왔습니다.
따라서 사람의 풀이 능력이나 부정행위 방지 수준을 측정하지 않았습니다.

레시피는 획득·제시·보류·시작·토핑·완료 요청을 실행했습니다.
토핑은 `thanks`와 `promise`로 나눴습니다.
앞 단계 요청이 실패한 계정의 후속 409는 별도 거절로 구분했습니다.
매니저 업적은 마지막 조건을 검사하려고 나머지 누적 조건을 미리 저장했습니다.
빼빼로 충전·적중·소모·잔량 1 대화는 실제 소켓 요청으로 실행했습니다.
공용 공간의 한 계정은 공격이 빗나가 잔량 1 조건에 도달하지 못했습니다.
해당 계정의 후속 업적 대화는 요청하지 않았습니다.

## 6. DB 일관성과 재시작 검증

| 검사 | 실제 결과 |
| --- | --- |
| 이스터에그 완료 기록 | {audit['egg_count']}개 |
| 계정별 중복 업적 | {len(audit['duplicate_users'])}건 |
| 이스터에그별 중복 당첨 | {len(audit['duplicate_eggs'])}건 |
| 완료 시각·순위 NULL | {audit['invalid_egg_metadata']}건 |
| 이스터에그 완료 순위가 1이 아닌 기록 | {audit['egg_order_not_one']}건 |
| 업적 접속 UUID 누락 | {audit['eggs_missing_session']}건 |
| 메인 최종 전달 완료 | {audit['main_completed']}계정 |
| 메인 순위 중복 없음 / 연속 | {audit['main_rank_unique']} / {audit['main_ranks_contiguous']} |
| 메인 완료 접속 UUID 누락 | {audit['main_missing_session']}건 |
| 레시피 완료 저장 | {audit['recipe_completed']}계정 |
| 재시작 후 실제 DB 상태 일치 | {r['persistence']['passed']}/100계정 |
| 재시작 전후 진행·스토리 이력 해시 동일 | {r['persistence']['progress_and_history_unchanged']} |

게임 결과 재전송 시 `ended_at`을 첫 응답과 비교했습니다.
이미 받은 업적의 완료 시각과 완료 순위도 비교했습니다.
메인 최종 전달 재전송 시 순위가 바뀌지 않는지 확인했습니다.
학습 답 재전송은 같은 요청 UUID로 실행했습니다.

재시작 검사는 부하 테스트와 분리했습니다.
완료하지 못한 계정도 DB에 저장된 실제 상태를 기준으로 비교했습니다.
완료 상태를 강제 가정하지 않았습니다.
서버 재시작 검사의 HTTP 동시 실행 수는 10개입니다.
따라서 이 검사는 100개 동시 HTTP 요청의 성능 검사가 아닙니다.

## 7. 자원 사용과 오류 원인

{table(['부하','프로세스','CPU P95 %','CPU 최대 %','메모리 최대 MiB','DB 연결 최대'],resources)}

CPU 100%는 논리 CPU 한 개를 사용하는 수준입니다.
메모리는 프로세스 RSS이며 DB와 생성기 메모리는 포함하지 않습니다.
자원 표본 간격은 2초입니다.
짧은 DB 연결 급증은 자원 표에서 누락될 수 있습니다.

### 확인한 실패

1. **DB 연결 한도 초과**
   - 동시 API 요청에서 `FATAL: sorry, too many clients already`를 확인했습니다.
   - 영향 경로: {', '.join('`'+x+'`' for x in errors)}.
   - 실제 서버와 테스트 서버의 연결 상한은 모두 100입니다.
   - 연결 풀과 동시 DB 작업 제한을 검토할 필요가 있습니다.

2. **제어 요청과 채팅의 대기 증가**
   - 높은 부하에서 10초 초과와 미응답이 발생했습니다.
   - 서버 로그에서도 세션 검사와 위치 저장의 DB 대기를 확인했습니다.
   - 소켓 DB 작업은 코드상 한 스레드에서 처리합니다.
   - 다만 CPU 프로파일링으로 전체 병목 비중을 분리하지는 않았습니다.

### 별도 계측으로 확인한 쿼리 수

기본 P95 측정을 마친 뒤 테스트 백엔드에만 계측 코드를 추가했습니다.
먼저 각 조회를 10회씩 순서대로 호출했습니다.
아래 SQL 시간은 DB 호출의 왕복 시간을 합한 값입니다.
운영 코드 파일은 수정하지 않았습니다.

{table(['조회 경로','N','요청당 SQL 수','프로필 개별 조회 수','SQL 합계 P95'],query_rows)}

메인 진행과 순위 응답에서 프로필을 최대 25회 개별 조회했습니다.
이는 순위에 표시할 닉네임을 한 명씩 불러오는 구조와 일치합니다.
메인 진행 조회는 이벤트·게임 정의와 진행 상태도 반복 조회합니다.
100명이 함께 조회하면 이런 DB 호출이 겹칩니다.
다만 쿼리 개수만으로 전체 지연의 비중을 확정할 수는 없습니다.

### 소켓 DB 작업의 실제 대기

별도 100명 진단 부하를 60초 실행했습니다.
아래 표는 완료한 DB 작업의 대기와 실행 시간을 나눴습니다.
대기에는 실행 스레드에 들어가기 전의 시간이 포함됩니다.
계측 부하가 추가되므로 기본 180초 실험의 P95와 직접 합산하지 않았습니다.

{table(['DB 작업','N','진입 대기 P95','함수 실행 P95','SQL 합계 P95'],queue_rows)}

높은 입력량, 단일 백엔드 처리, DB 작업의 직렬 처리와 반복 조회가 함께 존재합니다.
각 원인의 전체 지연 기여율은 추가 프로파일링이 필요합니다.
우선 연결 수 제한, 반복 조회 축소, 소켓 작업 대기를 개선할 필요가 있습니다.

### 추가로 발견한 경로 계산 문제

100명에게 긴 이동 경로를 동시에 계산시키는 탐색 검사도 수행했습니다.
목적지 도착은 0/100이었고 40초 요청 시간 초과가 발생했습니다.
테스트 백엔드 CPU가 한 코어 수준까지 올라갔습니다.
이 검사는 기본 부하 P95에서 분리했습니다.
당시 Python 소켓 라이브러리의 자동 ping 제한도 연결 종료에 영향을 줬습니다.
이 종료를 모두 서버 장애로 집계하지 않았습니다.
후속 NPC 검사는 자동 ping을 끄고 기존 도구의 키 이동 방식을 사용했습니다.
경로 계산 병목의 정확한 CPU 비중은 미확정입니다.

기능별 실패 상세는 [시나리오 결과]({evidence}/scenario-checks.json)에 기록했습니다.
업무상 거절과 HTTP 장애는 구분해야 합니다.
앞 단계 오류로 진행하지 못한 계정을 데이터 손실로 집계하지 않았습니다.

## 8. 기능 회귀와 미검증 범위

백엔드 전체 테스트 종료 코드는 `{r['regression']['backend_exit']}`입니다.
프런트 테스트 종료 코드는 `{r['regression']['frontend_exit']}`입니다.
백엔드는 **747건 중 688건 통과, 23건 실패, 29건 오류, 7건 건너뜀**입니다.
프런트는 **235건 모두 통과**했습니다.
백엔드 회귀는 통과로 판정하지 않았습니다.
전체 실패 목록은 [회귀 실패 목록]({evidence}/regression-failures.json)에 보관했습니다.

확인한 사례는 다음과 같습니다.

- 연속 찌르기 테스트가 `pepero_remaining` 초기값 없이 소비자 객체를 만들었습니다.
- 일부 계단 테스트는 현재 지도에 없는 `interactionPoint`를 참조했습니다.
- 레시피 라이브 테스트는 두 번째 계정도 같은 업적을 받아야 한다고 기대했습니다.
- 이 기대는 현재 이스터에그당 한 명 정책과 다릅니다.
- 광고 라우팅 일부에서는 `connection is closed` 오류가 발생했습니다.
- 소켓 인증·위치·점프 관련 실패도 남아 있습니다. 전체 원인은 확정하지 않았습니다.

테스트 전제 차이가 확인된 사례와 원인 미확정 사례를 구분해야 합니다.
52건을 모두 제품 결함이나 테스트 문제로 단정하지 않았습니다.
상세 결과는 [백엔드 로그]({evidence}/backend-regression.log), [프런트 로그]({evidence}/frontend-regression.log)를 참고하세요.
기능 회귀는 100명 부하 결과와 다른 종류의 증거입니다.

광고 200·201 처리, 중복 응답, 잘못된 응답, 롤백은 모의 응답으로 검사했습니다.
실제 광고 서버의 주소·키가 없어 외부 광고 연동 부하는 실행하지 않았습니다.
브라우저 100개, 화면 FPS, 렌더링 지연, 인터넷 지연, 운영 장비는 검사하지 않았습니다.
장시간 운영, DB 장애 전환, 프로세스 강제 종료와 복구도 이번 성능 측정에 포함하지 않았습니다.
운영 시간 제한은 기능 회귀에서 검사했습니다. 최종 부하는 미리보기 모드입니다.

## 9. 재현 방법과 산출물

테스트 코드는 [harness]({evidence}/harness/)에 보관했습니다.
경로와 포트는 테스트 전용으로 고정했습니다.
`setup.py`는 실행마다 계정 100개를 생성하므로 새 테스트 DB에서 실행해야 합니다.
실제 사용자 DB에 실행하면 안 됩니다.
기존 `loadtest/run.py`의 MySQL Docker 구성을 사용하지 않았습니다.

{reproduction}

최종 측정의 정확한 시작·종료 시각은 `results.json`에 저장했습니다.
요청 지연은 단조 증가 시계로 계산했습니다.
측정 구간과 자원 표본을 연결할 때는 UTC 시각을 사용했습니다.
보고서 P95는 원본 값으로 다시 계산했습니다.
처음 발견한 도구 문제와 탐색 결과는 최종 수치와 분리했습니다.
초기 도구의 채팅 시간 연결 오류와 핑 누락을 고친 뒤 최종 측정을 실행했습니다.

인증 쿠키, 비밀키, 서버 환경 파일은 산출물에 포함하지 않았습니다.
부하 테스트에서 발견한 서버 문제는 이번 작업에서 수정하지 않았습니다.
수정 후 같은 조건으로 다시 측정해야 개선 여부를 판단할 수 있습니다.
'''
if (OUT/'review.md').exists():
    document=document.replace('검토 상태: 독립 검토 대기','검토 상태: **2차 독립 검토 승인** · 서브에이전트 `stress_report_review`')
    document+=f'\n\n## 10. 독립 검토와 실행 환경 정리\n\n보고서는 1차 반려 후 필수 수정사항을 반영했습니다.\n2차 검토에서 승인받았습니다.\n[검토 기록]({evidence}/review.md)을 함께 보관했습니다.\n보고서 승인은 서버 성능 통과를 의미하지 않습니다.\n'
    if (OUT/'cleanup.json').exists():
        cleanup=json.loads((OUT/'cleanup.json').read_text())
        document+=f"\n테스트 앱과 테스트 DB는 종료했습니다.\n테스트 포트 8055·9055·55479의 종료를 확인했습니다.\n기존 서버 8004는 HTTP {cleanup['live_frontend_http_status']}으로 응답했습니다.\n"
(OUT/'stress-test.md').write_text(document)
print('stress-test.md generated')
