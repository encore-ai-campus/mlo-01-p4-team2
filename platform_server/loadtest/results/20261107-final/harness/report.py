"""Concise WS A/B report; preserve the approved SQL report separately."""
import json
from pathlib import Path
ROOT=Path('/private/tmp/platform-ws-20261106');REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
C=json.loads((ROOT/'comparison.json').read_text());A=C['variants']['front'];B=C['variants']['direct']
E='loadtest/results/20261106-ws'
L=['# 100계정 WS 경로 비교','',
 '- 검토: 독립 검토자 두 명의 승인 대기','',
 '## 경로와 고정 조건','',
 '| 조건 | 이동·채팅 WebSocket 경로 |',
 '| --- | --- |',
 '| A: 프런트 경유 | 클라이언트 → 입구 Nginx → Python 프런트 → 내부 Nginx → 백엔드 |',
 '| B: 프런트 우회 | 클라이언트 → 입구 Nginx → 내부 Nginx → 백엔드 |','',
 '두 조건 모두 연결 이후에도 두 Nginx를 경유합니다. 화면과 기존 HTTP 경로는 유지했습니다.',
 '완료된 SQL 최적화본, 이동4·서비스4·jobs1, 풀 최소1·최대10을 고정했습니다.',
 '이동의 least_conn과 서비스의 쿠키 hash 정책을 유지했습니다. 변경한 목적지는 WS 두 경로뿐입니다.',
 f'이동 프런트 대기열128·서비스{B.get("service_frontend_backlog",50)}, OS 상한128, PG17.7·Redis7.0.15를 유지했습니다.','',
 '입구와 내부 Nginx는 별도 프로세스입니다. 로컬 같은 PC에서 실행했고, 별도 PC 간 네트워크는 재현하지 않았습니다.',
 '두 조건 모두 Nginx별worker1개를 고정했습니다. 이전 SQL 시험의 Nginx 구성과 달라 해당 시험의 속도와 직접 비교하지 않습니다.',
 '부하는 localhost를 사용합니다. 별도로 사설 IP에서 Cookie·Origin을 포함한 두 WS 연결을 검사했습니다.',
 '백엔드와 내부 Nginx는127.0.0.1에 바인딩했습니다. 사설망 진입점은 입구8116입니다.','',
 '## 응답 P95와 단축 시간','',
 '| 부하 | 요청·측정 | A P95 ms | B P95 ms | 단축 ms | 단축 % |',
 '| --- | --- | ---: | ---: | ---: | ---: |']
for path,name in [('movement','이동 연결·재접속'),('chat','채팅 연결·재접속')]:
 d=C['connection_deltas'][path]
 if d:L.append(f"| 별도100계정 시험 | {name} | {d['before_ms']} | {d['after_ms']} | {d['saved_ms']} | {d['saved_pct']} |")
for phase,title in [('casual','일반'),('stress','높음')]:
 for kind,name in [('movement','이동 입력→첫 움직임 상태'),('chat','채팅 전송→자기 메시지 수신')]:
  d=C['deltas'][phase][kind]
  if d:L.append(f"| {title} | {name} | {d['before_ms']} | {d['after_ms']} | {d['saved_ms']} | {d['saved_pct']} |")
  else:L.append(f'| {title} | {name} | 표본 없음 | 표본 없음 | — | — |')
L.extend(['','이동은 시뮬레이터의 첫 움직임 상태 수신 추정값입니다. 서버 위치 저장 완료나 화면 표시 시간을 뜻하지 않습니다.',
 '채팅은 보낸 문구와 계정을 대조합니다. 음수 단축은 지연 증가입니다.','',
 '연결 P95는100계정의 두 번 접속, 소켓별200건입니다. welcome/chat_ready 수신까지 측정하며 사설 IP 단발 검사는 제외했습니다.','',
 '## 요청·오류 집계',''])
for phase,title in [('casual','일반'),('stress','높음')]:
 for label,r in [('A',A),('B',B)]:
  L.append(f"- {title} {label}: 이동 표본{r[phase]['movement_latency']['n']}개, 채팅 전송 계정{r[phase]['chat_accounts']}명, Chrome 채팅 연결{r[phase]['chrome_chat_open']}, 준비 누락{r[phase]['chat_missing_ready']}건.")
L.append('')
for title,r in [('A: 프런트 경유',A),('B: 프런트 우회',B)]:
 L.extend(['### '+title,'','| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 미확정 | P95 ms |',
  '| --- | ---: | ---: | ---: | ---: | ---: |'])
 groups={'반복 이동 접속':r['admission']['requests']}
 for phase,name in [('casual','일반'),('stress','높음')]:
  groups[name+' HTTP']=r[phase]['combined_http_terminal'];groups[name+' 채팅']=r[phase]['chat_terminal']
  groups.update({name+' '+action:v for action,v in r[phase]['control_terminal'].items()})
 for name,v in groups.items():L.append(f"| {name} | {v['attempts']} | {v['success']} | {v['errors']} | {v['unresolved']} | {(v['terminal_ms'] or {}).get('p95','—')} |")
 L.extend(['','| 인증·재접속 확인 | 시도 | 기대 일치 | 실패 | P95 ms |','| --- | ---: | ---: | ---: | ---: |'])
 for name,v in r['security'].items():
  if v['purpose']=='normal':L.append(f"| {name} | {v['attempts']} | {v['success']} | {v['errors']} | {v['p95']} |")
 L.extend(['','| 의도한 거절 검증 | 시도 | 예상대로 거절 | 검증 실패 | P95 ms |','| --- | ---: | ---: | ---: | ---: |'])
 for name,v in r['security'].items():
  if v['purpose']=='validation':L.append(f"| {name} | {v['attempts']} | {v['success']} | {v['errors']} | {v['p95']} |")
 L.append('')
L.extend(['## 연결 오류와 종료 예외','',
 '| 확인 항목 | A | B |','| --- | ---: | ---: |',
 f"| 입구502 | {A['edge_errors']['502']} | {B['edge_errors']['502']} |",
 f"| 입구504 | {A['edge_errors']['504']} | {B['edge_errors']['504']} |",
 f"| 종료 후 전송 예외: 실제 로그 레코드 수 | {A['closed_send_exceptions']['count']} | {B['closed_send_exceptions']['count']} |"])
for phase,title in [('casual','일반'),('stress','높음')]:
 L.append(f"| {title}: 측정 중 예상 밖 소켓 종료 | {sum(A[phase]['unexpected_closes'].values())} | {sum(B[phase]['unexpected_closes'].values())} |")
L.extend(['',
 '예외는 로그 레코드당 한 번씩 셌습니다. 연쇄 traceback과 소스 코드·오류 문구 반복 행은 중복 제외했습니다.',
 '종료 예외는 시험 전체 로그입니다. 소켓 종료 수나 실패 요청 수와 같지 않습니다.',
 'A의502는 높은 부하 Chrome 준비 구간의GET / 응답입니다. 혼합 API·접속 실패와 구분하며, 우회가 원인을 해결했다고 단정하지 않습니다.',
 '업무 조건 거절은 원본 코드로 보존했습니다. 정상 성공을 기대한 혼합 동작의 거절은 기대 불일치로 표시했습니다.',
 'A의 높은 부하 채팅1건은 `잠시 후 다시 보내 주세요.` 거절입니다. 서버5xx와 구분합니다.',
 '무쿠키·잘못된 쿠키·허용하지 않은 Origin·로그아웃한 쿠키는403을 기대했습니다. 예상 거절은 실제 오류가 아닙니다.',
 '로그아웃은 기존 HTTP 경로로 요청합니다. 열려 있던 두 소켓의4401 종료와 이전 쿠키 재접속 거절을 확인했습니다.','',
 '인증표의 정상 연결203건에는 사설 IP·로그아웃 준비 연결도 포함합니다. 상단 연결 속도의200건과 표본이 다릅니다.',
 '단발 검사의 P95는 대표 속도가 아닙니다. 로그아웃 종료 시간은HTTP 응답 후 종료 확인까지이며 실제 반영 지연과 다릅니다.','',
 '## 지연 해석','',
 '우회 조건은 Python 프런트의 중계 연결과 메시지 전달 단계를 제거합니다. Nginx와 백엔드·Redis 처리는 계속 유지됩니다.',
 '높은 부하의 이동·채팅 응답은 개선됐습니다. 채팅 연결 P95는 증가했으므로 모든 항목이 빨라졌다고 판단하지 않습니다.',
 '서버와 생성기가 같은 CPU를 공유했습니다. 중계 비용·CPU 경합·생성기 지연의 기여율을 따로 계측하지 않았습니다.','',
 '## 측정 방법과 한계','',
 '- 같은 템플릿에서100계정·세션·문제·진척을 복제했습니다. 군별 DB·Redis를 분리했습니다.',
 '- 두 군 모두 같은 최적화 소스와 계측기를 사용했습니다. 로그인·활동 시각만 갱신하고 쿠키 키·접속 UUID는 유지했습니다.',
 '- 혼합 부하는 프로토콜99계정·Chrome1계정입니다. 일반·높은 부하를 각각180초 측정했습니다.',
 '- 반복 접속은100계정×10회입니다. 이동·채팅 재접속 검증은 각 계정에서 두 번씩 수행했습니다.',
 '- 요청 수는 호출 시도입니다. 종료 시 새 요청을 막고 기존 요청을 최대65초 추적했습니다.',
 '- 확정된 전체 결과로 P95를 계산했습니다. 오류·시간초과를 포함하고 미확정에는 임의 시간을 넣지 않았습니다.',
 '- 혼합 HTTP 제한45초·WS10초입니다. 별도 접속·인증 검사 제한은40초입니다.',
 '- 운영 기간 전 시험을 위해 main.window만open으로 고정했습니다. 게임·시스템 시계는 바꾸지 않았습니다.',
 '- DEBUG0·광고0·세션 자동 청소0입니다. 기존 사용자 서버8004와 DB55474는 유지했습니다.',
 '- 같은 PC의 CPU·메모리를 공유합니다. A→B를 한 번씩 실행한 결과이며 운영망 성능을 보장하지 않습니다.',
 '- 사설 IP 검증도 같은 PC에서 수행했습니다. 외부 PC·TLS·물리 네트워크 지연은 검사하지 않았습니다.','',
 f'[원본·전체 집계]({E}/comparison.json) · [실행 하네스]({E}/harness/) · [검토 기록]({E}/reviews.json)',
 '[승인된 SQL 튜닝 전후 보고서](loadtest/results/20261106-query/report.md)는 변경하지 않고 보존했습니다.',
 '프런트 우회는 이번 로컬 시험 설정에만 적용했습니다. 실제 운영 배포 설정은 변경하지 않았습니다.'])
(REPO/'stress-test.md').write_text('\n'.join(L)+'\n')
print('WS comparison report written')
