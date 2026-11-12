"""Write compact API tables, preserving every final outcome and its denominator."""
import json
from pathlib import Path
ROOT=Path('/private/tmp/platform-query-20261106')
comparison=json.loads((ROOT/'comparison.json').read_text())
rules=json.loads((ROOT/'expectations.json').read_text())
lines=['# API별 요청 집계','',
 '요청 개수는 API 호출 시도입니다. 같은 계정의 재요청도 각각 셉니다.',
 '정상 처리는 사전에 정한 응답 코드와 주요 결과값을 비교합니다. 예상과 다른 응답만 실제 오류입니다.',
 '의도한 거절은 별도 검증입니다. 정확한 코드·문구·판정이 일치하면 검증 통과입니다.',
 'P95는 호출 시작부터 본문 수신·오류·시간초과 확정까지입니다. 실행 대기를 포함합니다.',
 '미확정은 P95에서 제외합니다. 요청=성공+오류+미확정을 검사했습니다.','']
lines.extend(['## 거절 검증의 사전 조건',''])
for action,spec in rules['validations'].items():
 lines.append(f"- `{action}`: 예상 코드 `{spec['statuses']}`, 예상 결과 `{spec['fields']}`.")
lines.extend(['','다른400이나 다른 거절 문구는 검증 실패입니다. 정상 처리와 거절 검증의 P95 표본은 섞지 않았습니다.',''])
def table(title,groups,validation=False):
 header='| 검증 항목 | 시도 개수 | 예상대로 거절 | 검증 실패 개수 | 반응속도(P95 ms) |' if validation else '| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |'
 lines.extend(['## '+title,'',header,'| --- | ---: | ---: | ---: | ---: |'])
 for name,r in groups.items():
  p=(r['terminal_ms'] or {}).get('p95','—')
  lines.append(f"| `{name}` | {r['attempts']} | {r['success']} | {r['errors']} | {p} |")
 lines.append('')
 for name,r in groups.items():
  if r['errors'] or r['unresolved']:
   lines.append(f"- `{name}`: 기대 불일치 {r['errors']}, 미확정 {r['unresolved']}. 원본 코드 분류는 comparison.json에 보존했습니다.")
 lines.extend(['',f"미확정 합계: {sum(r['unresolved'] for r in groups.values())}건.",''])
 lines.extend(['| 요청 | 실제 응답 코드별 개수 |','| --- | --- |'])
 for name,r in groups.items():
  codes=', '.join(f'{code}: {n}' for code,n in sorted(r['status_counts'].items()))
  lines.append(f'| `{name}` | {codes} |')
 lines.append('')
for label,display in [('before','쿼리 튜닝 전'),('after','쿼리 튜닝 후')]:
 r=comparison['variants'][label]
 for key,title in [('casual','일반 부하'),('stress','높은 부하')]:
  table(display+' / '+title+' / 실제 HTTP API',r[key]['combined_api_requests'])
  table(display+' / '+title+' / 소켓 요청',{'chat':r[key]['chat_terminal'],**r[key]['control_terminal']})
 table(display+' / 반복 접속',{'ws_movement_connect':r['admission']['requests']})
 table(display+' / 기능 API 동작별',{
  (', '.join(r['scenario_routes'][action]) or 'WS /ws/movement/')+' ['+action+']':value
  for action,value in r['normal_scenario_requests'].items()})
 table(display+' / 의도한 거절 검증',{
  (', '.join(r['scenario_routes'][action]) or 'WS /ws/movement/')+' ['+action+']':value
  for action,value in r['validation_requests'].items()},validation=True)
 missing={k:v for k,v in r['scenario_not_reached'].items() if v}
 if missing:
  lines.extend(['### 앞 단계 실패로 요청에 도달하지 못한 계정','',
   '아래 계정 수는 API 시도·오류 분모에 넣지 않았습니다.',''])
  for name,n in missing.items():lines.append(f'- `{name}`: {n}계정.')
  lines.append('')
def delta_table(title,groups):
 lines.extend(['## '+title,'',
  '절감은 튜닝 전에서 튜닝 후를 뺀 값입니다. 음수는 지연 증가입니다.','',
  '| 요청 | 전 요청 수 | 후 요청 수 | 전 P95 ms | 후 P95 ms | 절감 ms | 절감 % |',
  '| --- | ---: | ---: | ---: | ---: | ---: | ---: |'])
 for name,d in groups.items():
  if d:
   lines.append(f"| `{name}` | {d['before_attempts']} | {d['after_attempts']} | {d['before_p95']} | {d['after_p95']} | {d['saved_ms']} | {d['saved_pct']} |")
  else:lines.append(f'| `{name}` | — | — | — | — | — | — |')
 lines.append('')
for phase,title in [('casual','일반 부하'),('stress','높은 부하')]:
 delta_table(title+' / 요청별 변화',comparison['savings'][phase])
for purpose,title in [('normal','기능 요청별 변화'),('validation','거절 검증별 변화')]:
 delta_table(title,{action:d for action,d in comparison['scenario_savings'].items()
  if comparison['variants']['before']['scenario_requests'].get(action,{}).get('purpose')==purpose})
(ROOT/'api-results.md').write_text('\n'.join(lines)+'\n')
print('API tables prepared with complete outcome accounting')
