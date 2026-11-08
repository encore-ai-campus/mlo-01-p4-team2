import json,math,gzip,hashlib,collections,datetime,re,subprocess,psycopg
from pathlib import Path
R=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
E=R/'loadtest/results/20261107-http128'
T=Path('/private/tmp/platform-http128-20261107')
def p95(xs):
 xs=sorted(xs);return round(xs[math.ceil(.95*len(xs))-1],2) if xs else None
assert p95(list(range(1,101)))==95
def route(row):
 path=(row.get('uri') or row['path']).split('?')[0]
 path=re.sub(r'/sessions/\d+', '/sessions/:session_id', path)
 path=re.sub(r'/questions/\d+', '/questions/:order', path)
 return row.get('method','GET')+' '+path

def stats(rows,field='ms',ok=None):
 terminal=[r for r in rows if r.get('status') not in ('pending','unresolved',None)]
 passed=sum(ok(r) if ok else r.get('expected',False) for r in terminal)
 rejected=sum(ok is not None and isinstance(r['status'],int) and 400<=r['status']<500 for r in terminal)
 return dict(requests=len(rows),success=passed,errors=len(terminal)-passed-rejected,condition_rejected=rejected,unresolved=len(rows)-len(terminal),p95_ms=p95([r[field] for r in terminal]),codes=dict(collections.Counter(str(r['status']) for r in rows)))
D={};S={}
for label in ('a1','b1','b2','a2'):
 out=E/label;full=label in ('a1','b1');suffix='' if full else '-fullbank'
 rows=[json.loads(s) for s in (out/f'scenario-samples{suffix}.jsonl').read_text().splitlines()]
 checks=json.loads((out/f'scenario-checks{suffix}.json').read_text())
 S[label]=rows;cap=50 if label[0]=='a' else 128
 d=dict(backlog=cap,actions={a:stats([r for r in rows if r['action']==a]) for a in sorted({r['action'] for r in rows})},checks=checks)
 d['learning_completed']=next(c['passed'] for c in checks if c['phase']=='learning_all_subjects_and_types')
 assert d['actions']['learning:start']['requests']==100
 for port,expected in ((9581,128),(9582,cap)):
  line=next(x for x in (out/'listen-queues.txt').read_text().splitlines() if f'127.0.0.1.{port}' in x)
  assert int(line.split()[0].split('/')[-1])==expected
 attest=[json.loads(p.read_text()) for p in out.glob('attestation-*.json')]
 assert len(attest)==9 and all(v['pool_options']=={'min_size':1,'max_size':10} for v in attest)
 access=[s.split('|') for s in gzip.decompress((out/'native-access.log.gz').read_bytes()).decode().splitlines()]
 d['edge_errors']={code:sum(len(v)==7 and v[1]=='8116' and v[2]==code for v in access) for code in ('502','504')}
 if full:
  parts=[json.loads((out/f'raw-real-{i}.json').read_text()) for i in (0,1)]
  chrome=json.loads((out/'chrome-real.json').read_text())['metrics']
  assert sum(p['welcomed'] for p in parts)==99 and chrome['welcome']
  http=[v for p in parts for v in p['http_requests']]+[{**v,'terminal_ms':v.get('ms')} for v in chrome['http']]
  d['high_http']=stats(http,'terminal_ms',lambda v:v['status'] in (200,201))
  d['high_api']={path:stats([r for r in http if route(r)==path],'terminal_ms',lambda v:v['status'] in (200,201)) for path in sorted({route(r) for r in http})}
  d['high_chat']=stats([v for p in parts for v in p['chat_requests']],'terminal_ms',lambda v:v['status']=='ok')
  d['drain']=[v for p in parts for v in p['drain_reports']]
  assert all(v['unresolved']==0 for v in d['drain'])
  d['accounts']={'protocol':99,'chrome':1}
 D[label]=d
P={}
for cap,labels in ((50,('a1','a2')),(128,('b1','b2'))):
 rows=[r for k in labels for r in S[k] if r['action'].startswith('learning:')]
 P[str(cap)]={a:stats([r for r in rows if r['action']==a]) for a in sorted({r['action'] for r in rows})}
def delta(a,b):
 return {'before':a,'after':b,'saved_ms':round(a-b,2),'saved_pct':round((a-b)/a*100,2)}
C={'runs':D,'pooled_learning':P,'high_http_delta':delta(D['a1']['high_http']['p95_ms'],D['b1']['high_http']['p95_ms']),'learning_deltas':{a:delta(P['50'][a]['p95_ms'],P['128'][a]['p95_ms']) for a in P['50']}}
(E/'comparison.json').write_text(json.dumps(C,ensure_ascii=False,indent=2))
# Recover original failed preparation tool outputs from this session's audit log.
rollout=Path('/Users/sindong-yeob/.codex/sessions/2026/11/06/rollout-2026-11-06T11-50-13-01a1aec3-8ddb-7e61-b389-51c324f183fc.jsonl')
patterns={'owned_jobs_check':'AssertionError: /private/tmp/platform-event-postgres-venv/bin/python -B /Users/sindong-yeob/projects/platform-server/pipeline/tests/ws-20261106/runtime.py',
 'expired_session':"AssertionError: ('refresh_sessions.py', (), 1)",
 'redis_fixture_guard':"AssertionError: ('fixtures.py', ('casual',), 1)",
 'restored_baseline_cap':'AssertionError: 0/0/128'}
events={}
for line in rollout.open():
 try:r=json.loads(line)
 except ValueError:continue
 payload=r.get('payload',{})
 item=payload.get('item',{})
 if item.get('type')!='CommandExecution':continue
 output=item.get('aggregated_output','')
 if not isinstance(output,str):continue
 for name,pattern in patterns.items():
  if name not in events and pattern in output:
   events[name]={'timestamp':r.get('timestamp'),'source':'original Codex function_call_output','output':output}
assert set(events)==set(patterns),list(events)
(E/'preparation-failures.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
with psycopg.connect(host='127.0.0.1',port=55500,dbname='postgres',user='event_test') as db:
 databases=[r[0] for r in db.execute("SELECT datname FROM pg_database WHERE datname LIKE 'query_http128_%' ORDER BY datname")]
resources={'databases':databases,'active_processes':[]}
for name in ('pids.json','front-pids.json'):
 for row in json.loads((T/'a2'/name).read_text()):
  cmd=subprocess.check_output(['ps','-p',str(row['pid']),'-o','args='],text=True).strip()
  resources['active_processes'].append({'role':row['role'],'pid':row['pid'],'command':cmd})
for name in ('entry.pid','internal.pid'):
 pid=int((T/'a2'/name).read_text())
 resources['active_processes'].append({'role':name,'pid':pid,'command':subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True).strip()})
(E/'resource-state.json').write_text(json.dumps(resources,ensure_ascii=False,indent=2))
assert len(databases)==5,databases
l=['# 100계정 부하 테스트: HTTP·채팅 프런트 대기열50 →128','',
 '- 작성: '+datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
 '- 검토: 독립 검토자 두 명의 승인 대기','',
 '## 1. 결과','',
 '변경 변수는 HTTP·채팅 공용 Python 프런트9582의 새 연결 대기열입니다. 이동9581은128로 고정했습니다.',
 '전체 HTTP와 학습 완료 P95의 일관된 개선은 확인하지 못했습니다. 학습은 네 실행 모두100/100계정이 완료했습니다. 입구502·504도 두 조건에서 재현되지 않았습니다.',
 '따라서 오류 감소 효과나 과거504의 근본 원인 해결은 입증하지 못했습니다.','',
 '| 조건 | 높은 부하 HTTP 요청 | 성공 | 조건 거절 | 실제 오류 | 미확정 | P95 ms |',
 '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for label in ('a1','b1'):
 v=D[label]['high_http'];l.append(f"| {D[label]['backlog']} | {v['requests']} | {v['success']} | {v['condition_rejected']} | {v['errors']} | {v['unresolved']} | {v['p95_ms']} |")
v=C['high_http_delta']
l+=['',f"높은 혼합 HTTP P95는 {v['before']}→{v['after']}ms입니다. 절감은 {v['saved_ms']}ms ({v['saved_pct']}%)입니다.",
 '음수 절감은 지연 증가입니다. 전체 P95는 API 호출 비중에도 영향을 받습니다.','',
 '## 2. 학습 완료 반복 비교','',
 '순서는 A1(50)→B1(128)→B2(128)→A2(50)입니다. 실행마다 동일 템플릿에서 별도 DB를 복제했습니다.',
 'A1·B1은 메인·이스터에그·미니게임 뒤 학습했습니다. B2·A2는 학습만 수행했습니다.',
 '따라서 A1/B1과 A2/B2를 짝으로 비교합니다. 합산 P95는 두 실행의 원본 요청을 합쳐 계산했습니다.','',
 '| 실행 | 대기열 | 시작 계정 | 완료 계정 | 완료 요청 | 성공 | 실제 오류 | P95 ms |',
 '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
for label in ('a1','b1','b2','a2'):
 d=D[label];v=d['actions']['learning:complete'];l.append(f"| {label.upper()} | {d['backlog']} | 100 | {d['learning_completed']} | {v['requests']} | {v['success']} | {v['errors']} | {v['p95_ms']} |")
l+=['','| 요청 | 대기열 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |','| --- | ---: | ---: | ---: | ---: | ---: |']
for action in P['50']:
 for cap in ('50','128'):
  v=P[cap][action];l.append(f"| {action} | {cap} | {v['requests']} | {v['success']} | {v['errors']} | {v['p95_ms']} |")
l+=['','학습 완료'+' P95는 첫 짝에서 감소했지만 두 번째 짝에서는 증가했습니다. 일관된 개선으로 단정하지 않습니다.','',
 '## 3. 메인·이스터에그·미니게임','',
 'A1·B1에서 각각100계정을 검사했습니다. 성공은 사전 기대 응답과 필드의 일치이며 신규 업적 지급 수와 다릅니다.',
 '메인 선행 단계는 입력한 상태입니다. 자연스러운 전체 플레이100명 시험은 아닙니다.','',
 '| 요청 | 대기열 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |','| --- | ---: | ---: | ---: | ---: | ---: |']
for title,action in [('메인 최초 전달','main:deliver:first'),('메인 전달 재요청','main:deliver:retry'),('레시피 완료','recipe:finish'),('타이머 결과','timer:result'),('타이핑 결과','typing:result'),('페어링 결과','term_match:result')]:
 for label in ('a1','b1'):
  v=D[label]['actions'][action];l.append(f"| {title} | {D[label]['backlog']} | {v['requests']} | {v['success']} | {v['errors']} | {v['p95_ms']} |")
l+=['','## 4. 의도한 거절·오류·계정 분모','',
 '| 검증 항목 | 대기열 | 시도 개수 | 예상대로 처리 | 검증 실패 개수 | P95 ms |','| --- | ---: | ---: | ---: | ---: | ---: |']
for label in ('a1','b1'):
 for action,v in D[label]['actions'].items():
  rows=[r for r in S[label] if r['action']==action]
  if rows[0].get('expectation',{}).get('purpose')=='validation':
   l.append(f"| {action} | {D[label]['backlog']} | {v['requests']} | {v['success']} | {v['errors']} | {v['p95_ms']} |")
l+=['','| 실행 | 입구502 | 입구504 | 학습 미완료 계정 |','| --- | ---: | ---: | ---: |']
for label,d in D.items():l.append(f"| {label.upper()} | {d['edge_errors']['502']} | {d['edge_errors']['504']} | {100-d['learning_completed']} |")
l+=['','A1의 HTTP429 세 건은 모두 POST /api/messages/send/ 호출 제한 거절입니다. 서버5xx와 분리했습니다.',
 '입구 오류는 기능·Chrome 준비·부하 측정 구간 전체 로그입니다. 높은 부하 HTTP 표는 측정 구간만 집계합니다.',
 '학습1000문제 조회·정답 제출·같은 UUID 재제출과 완료를 요청했습니다. 완료 응답의 정답10개·완료 시각도 검증했습니다.',
 '완료 API에 미도달한 계정은 완료 요청 오류 분모에서 제외하고 전체 완료율에 반영합니다. 이번 미도달은0명입니다.',
 '소켓 재접속 뒤 메인·레시피·업적 상태와 DB 기준 스냅샷의 일치를 확인했습니다. 단계별 성공 수는 원본 checks에 있습니다.','',
 '## 5. 높은 부하의 동일 API 비교','',
 '| API | 대기열 | 요청 개수 | 성공 개수 | 조건 거절 | 실제 오류 개수 | P95 ms |','| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for path in sorted(set(D['a1']['high_api'])|set(D['b1']['high_api'])):
 for label in ('a1','b1'):
  if path not in D[label]['high_api']:continue
  v=D[label]['high_api'][path];l.append(f"| {path} | {D[label]['backlog']} | {v['requests']} | {v['success']} | {v['condition_rejected']} | {v['errors']} | {v['p95_ms']} |")
l+=['','## 6. 재현 조건·제외·한계','',
 '- SQL 최적화본·이동4·서비스4·jobs1·PostgreSQL 풀 최소1/최대10을 고정했습니다.',
 '- PG17.7·Redis7.0.15, 같은 M1 Pro·CPU8·RAM16GB에서 서버와 부하 생성기가 자원을 공유했습니다.',
 '- 이동·채팅 WS는 Python 프런트를 우회합니다. 따라서 이번 설정은 실제 HTTP 진입점에 적용됩니다.',
 '- 입구·내부 Nginx는 별도 프로세스이며 각각 worker1개입니다. 서비스 쿠키 hash와 이동 least_conn을 유지했습니다.',
 '- 높은 혼합 부하는 조건별180초입니다. 접속 증가15초·준비15초는 측정 표본에서 제외했습니다.',
 '- 높은 혼합 부하는 프로토콜99계정과 독립 headless Chrome1계정입니다. 기능 시험은 프로토콜100계정입니다.',
 '- 각 실행의 쿠키 키·초기 계정·문제·진척 수가 같습니다. 만료 세션은 같은 템플릿에서 복원하고 활동 시각을 갱신했습니다.',
 '- 별도 DB 네 개와 시험 전용 Redis 두 개를 사용했습니다. 각 실행 전 Redis를 비웠습니다.',
 '- 종료 시 신규 요청을 막고 기존 요청의 결과를 최대65초 추적했습니다. 미확정에 임의 시간을 넣지 않았습니다.',
 '- P95는 확정된 전체 요청을 정렬한 ceil(0.95×N)번째 값입니다. 오류·시간초과도 포함합니다.',
 '- 준비 실행 실패4회는 별도 보존했습니다. 작업 프로세스 확인·세션 만료·Redis 허용 목록·복구된 기준값 확인 문제였습니다.',
 '- 준비 실패는 실제100계정 성능 요청을 발행하기 전에 발생했습니다. 준비 실패4회를 API 오류 분모에 합산하지 않았습니다.',
 '- 두 조건의 학습은 두 번씩, 높은 혼합 부하는 한 번씩입니다. 일반 혼합 부하는 이번에 재시험하지 않았습니다.',
 '- 큐 점유·overflow·TCP 재전송은 계측하지 않았습니다. 속도 변화가 대기열 확대만의 효과라고 확정하지 않습니다.',
 '- 첫 기능 시험과 학습 단독 재시험의 선행 부하가 다릅니다. 합산 수치만으로 일관된 개선을 주장하지 않습니다.',
 '- 이전 SQL 보고서의 Nginx 구성과 다르므로 과거504 세 건과 직접 전후 개선을 계산하지 않습니다.',
 '- 외부 PC·TLS·운영망·광고·장시간·DB/Redis 장애·전체 회귀는 이번 범위에 포함하지 않았습니다.',
 '- 기존 사용자8004 서버·사용자 DB55474·과거 승인 보고서는 변경하지 않았습니다.','',
 '## 7. 종료 상태와 근거','',
 '마지막 실행 뒤 HTTP·채팅9582를128로 복구했습니다. 이동9581도 실제128임을 확인했습니다.',
 '종료한 자원: A1/B1/B2의 앱·프런트·Nginx 프로세스와 부하 생성기·전용 Chrome입니다.',
 '계속 점유하는 자원: 최종 A2의 이동4·서비스4·jobs1·프런트2·Nginx2, Redis57541/57542, DB55500의 측정 DB 네 개와 준비 실패 DB 한 개입니다.',
 '준비 실패 DB는 query_http128_a1_20261107이며 최종 측정 A1은 a1_20261107_v2를 사용했습니다. 기존 비교용 Redis·DB는 별도 보존 상태입니다. 시험 데이터 삭제나 운영 배포는 수행하지 않았습니다.','',
 '[원본·전체 집계](loadtest/results/20261107-http128/comparison.json) · [초기 조건](loadtest/results/20261107-http128/initial.json) · [준비 실패 원본](loadtest/results/20261107-http128/preparation-failures.json)',
 '[종료 자원 확인](loadtest/results/20261107-http128/resource-state.json) · [최종 실제 대기열](loadtest/results/20261107-http128/final-listen-queues.txt) · [실행 코드](loadtest/results/20261107-http128/harness/)',
 '실행별 a1/b1/b2/a2에 원본 요청·단계 검사·Nginx 로그를 보존했습니다. 쿠키·비밀 값은 제외했습니다.',
 '보고서 승인은 운영 성능 통과를 뜻하지 않습니다.','']
tail=[]
for cap,labels in ((50,('a1','a2')),(128,('b1','b2'))):
 for action in ('learning:question','learning:answer'):
  values=sorted(r['ms'] for label in labels for r in S[label] if r['action']==action)
  tail.append({'backlog':cap,'action':action,'n':len(values),'over_5000_ms':sum(v>5000 for v in values),'p99_ms':round(values[math.ceil(.99*len(values))-1],2),'max_ms':round(max(values),2)})
(E/'latency-tail.json').write_text(json.dumps(tail,indent=2))
pos=l.index('## 3. 메인·이스터에그·미니게임')
extra_lines=['학습 조회·정답 제출에서5초를 넘긴 요청은50조건38건,128조건0건이었습니다.',
 '50조건의 긴 지연은A2에 집중됐습니다. 이 관측만으로 안정적인 개선이나 특정 원인을 확정하지 않습니다.','',
 '| 요청 | 대기열 | 요청 개수 | 5초 초과 | P99 ms | 최대 ms |',
 '| --- | ---: | ---: | ---: | ---: | ---: |']
for v in tail:extra_lines.append(f"| {v['action']} | {v['backlog']} | {v['n']} | {v['over_5000_ms']} | {v['p99_ms']} | {v['max_ms']} |")
l[pos:pos]=extra_lines+['']

report=R/'stress-test-with-conn128.md';report.write_text('\n'.join(l));(E/'report.md').write_text(report.read_text())
print(json.dumps({'high_http_delta':C['high_http_delta'],'learning_deltas':C['learning_deltas'],'edge_errors':{k:v['edge_errors'] for k,v in D.items()}},ensure_ascii=False))
