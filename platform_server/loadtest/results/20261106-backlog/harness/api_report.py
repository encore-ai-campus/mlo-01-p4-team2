"""Write compact API tables, preserving every final outcome and its denominator."""
import json
from pathlib import Path
ROOT=Path('/private/tmp/platform-backlog-20261106')
comparison=json.loads((ROOT/'comparison.json').read_text())
lines=['# API별 요청 집계','',
 '요청 개수는 API 호출 시도입니다. 같은 계정의 재요청도 각각 셉니다.',
 '성공은 HTTP 2xx·WS 정상 결과입니다. 게임 승리나 신규 업적 지급과는 다릅니다.',
 '오류는 업무 거절·서버 오류·전송 오류·시간초과의 합입니다. 의도한 거절 검사도 포함합니다.',
 'P95는 호출 시작부터 본문 수신·오류·시간초과 확정까지입니다. 실행 대기를 포함합니다.',
 '미확정은 P95에서 제외합니다. 요청=성공+오류+미확정을 검사했습니다.','']
def table(title,groups):
 lines.extend(['## '+title,'','| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |','| --- | ---: | ---: | ---: | ---: |'])
 for name,r in groups.items():
  p=(r['terminal_ms'] or {}).get('p95','—')
  lines.append(f"| `{name}` | {r['attempts']} | {r['success']} | {r['errors']} | {p} |")
 lines.append('')
 for name,r in groups.items():
  if r['errors'] or r['unresolved']:
   lines.append(f"- `{name}`: 업무 거절 {r['business_rejected']}, 서버 오류 {r['server_error']}, 전송 오류 {r['network_error']}, 시간초과 {r['timeout']}, 미확정 {r['unresolved']}.")
 lines.extend(['',f"미확정 합계: {sum(r['unresolved'] for r in groups.values())}건.",''])
for label,display in [('baseline','A: 대기열50'),('increased','B: 대기열128')]:
 r=comparison['variants'][label]
 for key,title in [('casual','일반 부하'),('stress','높은 부하')]:
  table(display+' / '+title+' / 실제 HTTP API',r[key]['combined_api_requests'])
  table(display+' / '+title+' / 소켓 요청',{'chat':r[key]['chat_terminal'],**r[key]['control_terminal']})
 table(display+' / 반복 접속',{'ws_movement_connect':r['admission']['requests']})
 table(display+' / 기능 API 동작별',{
  (', '.join(r['scenario_routes'][action]) or 'WS /ws/movement/')+' ['+action+']':value
  for action,value in r['scenario_requests'].items()})
 missing={k:v for k,v in r['scenario_not_reached'].items() if v}
 if missing:
  lines.extend(['### 앞 단계 실패로 요청에 도달하지 못한 계정','',
   '아래 계정 수는 API 시도·오류 분모에 넣지 않았습니다.',''])
  for name,n in missing.items():lines.append(f'- `{name}`: {n}계정.')
  lines.append('')
(ROOT/'api-results.md').write_text('\n'.join(lines)+'\n')
print('API tables prepared with complete outcome accounting')
