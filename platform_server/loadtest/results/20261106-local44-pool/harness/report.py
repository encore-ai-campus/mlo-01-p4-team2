"""Keep the report concise; preserve full request metrics and logs in linked evidence."""
import json
from datetime import datetime
from pathlib import Path
ROOT=Path('/private/tmp/platform-local44-20261106')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
c=json.loads((ROOT/'comparison.json').read_text());old=c['old'];a=c['variants']['nopool'];b=c['variants']['pool']
E='loadtest/results/20261106-local44-pool/'
review=json.loads((ROOT/'reviews.json').read_text()) if (ROOT/'reviews.json').exists() else {}
lines=[]
def add(s=''):lines.append(s)
def table(headers,rows):
 add();add('| '+' | '.join(headers)+' |');add('| '+' | '.join(['---']*len(headers))+' |')
 for row in rows:add('| '+' | '.join(str(v).replace('|','/') for v in row)+' |')
 add()
def ms(v):return '—' if not v else f"{v['p95']:,.2f}"
def change(x,y):
 v=(y['p95']/x['p95']-1)*100
 return f'{abs(v):.1f}% '+('단축' if v<0 else '증가' if v>0 else '동일')
def http(r,k):return r[k]['http_started']['response_ms']
add('# 100계정 부하 테스트: 4+4·DB 연결 풀 적용 비교');add()
add('- 작성일: '+datetime.now().astimezone().isoformat(timespec='seconds'))
add('- 검토: **두 독립 검토자 모두 승인**' if review.get('both_approved') else '- 검토: **두 독립 검토자의 승인 대기**')
add('- 코드: event `c6290de` + 연결 풀 변경. A는4+4·풀 미사용, B는4+4·프로세스별 최대10개입니다.')
add()
add('## 1. 결론');add()
add('**HTTP 응답은 빨라졌고 DB 연결 상한 오류는 사라졌습니다. 502 개선은 확인하지 못했습니다.**')
table(['HTTP 응답 P95','기존3+3','A','B','기존 대비 B','A 대비 B'],[
 (name,ms(http(old,k)),ms(http(a,k)),ms(http(b,k)),change(http(old,k),http(b,k)),change(http(a,k),http(b,k)))
 for k,name in [('casual','일반 부하 ms'),('stress','높은 행동 부하 ms')]])
table(['검사','A','B'],[
 ('기능 HTTP 5xx / 응답',f"{a['scenario_http']['server_5xx']}/{a['scenario_http']['responses']}",f"{b['scenario_http']['server_5xx']}/{b['scenario_http']['responses']}"),
 ('반복 접속502 / 시도',f"{a['admission']['statuses'].get('502',0)}/1000",f"{b['admission']['statuses'].get('502',0)}/1000"),
 ('기능 단계 접속502 / 시도',f"{a['functional_ws']['errors_502']}/{a['functional_ws']['attempts']}",f"{b['functional_ws']['errors_502']}/{b['functional_ws']['attempts']}"),
 ('PG 전체 연결 관측 최대',a['all_phases_connections']['cluster_max'],b['all_phases_connections']['cluster_max'])])
add('반복 접속502는8.9%→6.7%였지만, 기능 단계에서는7.5%→22.5%로 늘었습니다. **운영 수용은 보류합니다.**')
add('풀은 DB 연결 부족을 조절합니다. 이번에 남은 프런트 접속 실패까지 해결한 것은 아닙니다.')
add()
add('## 2. 무엇을 비교했나');add()
add('- 동일4+4에서 풀·소켓 연결 반환·건강 검사 정책을 함께 적용한 효과를 비교했습니다.')
add('- A 소켓은 연결을60초 재사용합니다. B는 작업 후 풀에 반환하며, HTTP의 CONN_MAX_AGE는 둘 다0입니다.')
add('- B는 최소1·최대10개이며, 풀 대기 제한은30초입니다. 앱8개+jobs1개의 이론상한90개 외에 관리 연결도 필요합니다.')
add('- 공통 DB 템플릿을 복사했습니다. 계정 UUID·세션·문제3,600개·선행 진척은 같고, Redis는 군별 빈 인스턴스입니다.')
add('- 두 군은99개 프로토콜 계정+실제 headless Chrome1개로 일반·높은 행동 부하를 각각180초 측정했습니다.')
add('- 접속 증가15초·준비15초를 제외했습니다. 반복 접속은 같은100계정을10회 시험했습니다.')
add('- M1 Pro·CPU8·RAM16GB, Docker 메모리7.65GB를 공유했습니다. PG17.7·상한100·shared_buffers32MB를 유지했습니다.')
add('- A 다음 B를1회씩 실행했습니다. 시스템 상태·문항·게임 조건에 따른 변동이 남아 있어 통계적 우열을 확정하지 않습니다.')
add('- 기존3+3 대비에는 프로세스 수 변경도 포함됩니다. 기간 전 시험을 위해 main.window만 open으로 바꿨습니다.')
add('- DEBUG=0, 세션 자동 청소는 비활성화했습니다. 시스템·게임 경과 시간은 바꾸지 않았습니다.')
add('기존8004 서버와 사용자 DB55474는 유지했습니다. 변경한 앱·테스트 서버는 전용8096·DB55480입니다.')
add()
add('## 3. 응답·미응답과 주요 완료 요청');add()
table(['구간','A HTTP 시작 / 응답 / 미응답','B HTTP 시작 / 응답 / 미응답'],[
 (name,f"{a[k]['http_started']['started']} / {a[k]['http_started']['response_ms']['n']} / {a[k]['http_started']['no_response_at_end']}",
 f"{b[k]['http_started']['started']} / {b[k]['http_started']['response_ms']['n']} / {b[k]['http_started']['no_response_at_end']}")
 for k,name in [('casual','일반'),('stress','높은 행동')]])
add('P95는 응답·실패 종료 표본의 ceil(0.95×N)번째 값입니다. 미응답이 있으면 전체 시작 요청의 정확한 P95는 확정하지 않습니다.')
add('혼합 HTTP는 네트워크 실행부터 본문 수신까지입니다. 생성기 실행 대기는 제외하며 client_total_ms로 별도 보존했습니다.')
add('아래 기능 API 시간은 실행 대기도 포함합니다. 서로 다른 측정 범위를 구분해야 합니다.')
labels=[('main:deliver','메인 최종 전달·재요청'),('recipe:finish','레시피 완료'),('learning:complete','학습 완료'),
 ('timer:result','타이머 결과'),('typing:result','타이핑 결과'),('term_match:result','페어링 결과')]
table(['요청','A N / P95 ms','B N / P95 ms'],[
 (name,f"{x['n']} / {x['p95']:,.2f}",f"{y['n']} / {y['p95']:,.2f}") for key,name in labels
 for x,y in [(a['scenario-summary'][key],b['scenario-summary'][key])]])
add('접속·앞 단계 실패로 실제 요청 수가 다릅니다. 특히 게임 결과의 P95 변화에는 요청 수 차이도 영향을 줍니다.')
add('메인은8개 선행 단계를 사전 입력했습니다. 토핑은 실패 경로이며, 선행 완료값으로 포장·전달을 별도 검사했습니다.')
add('자연스러운 메인 전 과정100명 성공을 검증한 것은 아닙니다. 학습은3과목·객관식·빈칸과 답변 UUID 재요청을 검사했습니다.')
add('레시피는 두 분기를 시험했습니다. 신규 업적 지급 N=1의 시간은 단일 관측값이며 대표 P95가 아닙니다.')
add()
add('## 4. DB 제한과 저장 검증');add()
add(f"B 서비스 프로세스4개의 연결 관측 최대는 각각10개였습니다. 풀 대기는 최대{max(v['waiting_max'] for v in b['pool_stats'].values())}개 관측됐습니다.")
add('자원은 약2초·풀 통계는 약1초 간격이므로 순간 피크를 놓칠 수 있습니다. 관측·관리 연결도 PG 전체 수에 포함합니다.')
table(['백엔드 로그 문자열','A 줄 수','B 줄 수'],[(k,a['backend_error_lines'][k],b['backend_error_lines'][k]) for k in ['connection_limit','pool_timeout']])
add('connection_limit은 too many clients already, pool_timeout은 PoolTimeout 문자열입니다. 중복 줄을 실패 요청 수로 세지 않았습니다.')
add(f'[A 오류 발췌]({E}nopool/server-errors.txt) · [B 오류 발췌]({E}pool/server-errors.txt). 군별 backend-logs/*.gz에 전체8개 로그도 보존했습니다.')
add('- 관련 PostgreSQL 검사33개 통과: 연결20개 요청의 최대10개 공유, 성공·예외 반환, 끊긴 연결 교체와 업적 동시성.')
add('- B 메인·레시피·학습 완료는 각각100계정입니다. 중복 업적·중복 순위·완료 접속 UUID 누락은 없었습니다.')
add('- B 소켓 재접속77/100과, 소켓 성공 여부와 무관한 HTTP 상태 조회100/100은 다른 검사입니다.')
if 'persistence' in b:
 add(f"- 앱 재시작 후 HTTP 동시10개로 {b['persistence']['passed']}/100계정 상태를 비교했습니다. 진행·스토리 기록의 행 수와 해시도 같았습니다.")
add('순위의 프로필 추가 조회와 공통 행 잠금은 유지했습니다. 연결 풀만으로 모든 지연 원인이 사라지지는 않습니다.')
add()
add('## 5. 근거와 한계');add()
add(f'[전체 요청·회차별 수치]({E}comparison.json) · [초기 조건]({E}initial-comparison.json) · [검사 로그]({E}focused-tests-final.log) · [코드 변경]({E}application-change.patch)')
add(f'[기존3+3 보고서](loadtest/results/20261106-local33/report.md)와 원본은 보존했습니다. 예비 시험은 최종 비교에서 제외했습니다.')
add('전체747개 회귀 검사는 다시 실행하지 않았습니다. 이전 결과는 실패25·오류29·건너뜀7이었습니다.')
add('장시간 운영·실제 운영망·100개 Chrome·DB/Redis 장애·광고 연동·매니저 경로는 이번 재시험 범위가 아닙니다.')
add('다음 명령은 원본을 변경하지 않고 P95·분모·연결 상한·전체 로그와 발췌·파일 해시를 검산합니다. 서버·DB에는 요청하지 않습니다.')
add();add('```sh');add(f'python3 {E}harness/verify_compare.py {E}');add('```');add()
add('metrics.py·compare.py는 결과 JSON을 덮어씁니다. 재생성은 복사본에서만 실행해야 합니다.')
if review.get('both_approved'):
 add(f'두 독립 검토자가 모두 승인했습니다. [검토 기록]({E}reviews.json)을 첨부했습니다.')
 add('승인 후에는 승인 표기·검토 링크만 추가했습니다. 보고서 승인은 운영 성능 통과를 뜻하지 않습니다.')
else:add('두 독립 검토자가 모두 승인할 때만 최종본으로 표시합니다.')
(REPO/'stress-test.md').write_text('\n'.join(lines)+'\n');print('Concise comparison report rebuilt:',len(lines),'lines')
