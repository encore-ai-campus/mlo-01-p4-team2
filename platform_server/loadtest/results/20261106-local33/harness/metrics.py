"""Recompute percentiles and censored-request counts from raw measurements."""
from collections import Counter
import json
import math
from pathlib import Path
import re

OUT=Path('/private/tmp/platform-local33-20261106')
def percentiles(values):
    values=sorted(values)
    if not values:return None
    pick=lambda q:round(values[max(0,math.ceil(q*len(values))-1)],2)
    return {'n':len(values),'p50':pick(.5),'p95':pick(.95),'p99':pick(.99),'max':round(values[-1],2)}

assert percentiles(list(range(1,101)))['p95']==95
assert percentiles([7])['p99']==7
assert percentiles([]) is None

def outcomes(rows):
    counts=dict(Counter(row['status'] for row in rows))
    responses=[row['response_ms'] for row in rows if 'response_ms' in row]
    # Unanswered requests contribute conservative wait bounds, never invented response times.
    bounds=[row.get('response_ms',row.get('observed_ms',10000 if row['status']=='timeout' else 0)) for row in rows]
    return {'started':len(rows),'outcomes':counts,'response_ms':percentiles(responses),
        'p95_lower_bound_ms':(percentiles(bounds) or {}).get('p95'),
        'late_responses':sum(row['status']=='timeout' and 'response_ms' in row for row in rows),
        'no_response_at_end':sum('response_ms' not in row for row in rows)}

def http_outcomes(rows):
    result=outcomes(rows)
    result.pop('p95_lower_bound_ms')
    result['timing_origin']='fetch network execution; executor queue excluded'
    return result

def mixed(mode,log_name):
    text=(OUT/log_name).read_text()
    summary=next(json.loads(line) for line in reversed(text.splitlines()) if line.startswith('{'))
    parts=[json.loads((OUT/f'raw-{mode}-{i}.json').read_text()) for i in range(2)]
    result={'generator_summary':summary,'reference_movement_ms':{},'http_ms':{},'controls':{},'chat':{}}
    for key,label in [('move_lag','input_to_first_moving_state_estimate'),('state_gap','own_state_interval'),('peer_gap','other_player_update_interval'),('loop_lag','generator_event_loop_lag')]:
        result['reference_movement_ms'][label]=percentiles([v*1000 for part in parts for v in part[key]])
    for path in sorted({path for part in parts for path in part['http_ms']}):
        result['http_ms'][path]=percentiles([v*1000 for part in parts for v in part['http_ms'].get(path,[])])
    result['http_all_ms']=percentiles([v*1000 for part in parts for values in part['http_ms'].values() for v in values])
    rows=[row for part in parts for row in part['control_requests']]
    for action in sorted({row['action'] for row in rows}):
        result['controls'][action]=outcomes([row for row in rows if row['action']==action])
    result['chat']=outcomes([row for part in parts for row in part['chat_requests']])
    result['http_statuses']=summary['http_status']
    result['http_unexpected']=sum(n for key,n in summary['http_status'].items() if key.rsplit(' ',1)[-1] not in ('200','201'))
    start=float(re.search(r'MEASURE_START ([0-9.]+)',text)[1]);end=float(re.search(r'MEASURE_END ([0-9.]+)',text)[1])
    result['window_utc_epoch']=[start,end]
    resources=[json.loads(line) for line in (OUT/'resources.jsonl').read_text().splitlines() if line.strip()]
    resources=[r for r in resources if start<=r['at']<=end]
    result['resources']={'samples':len(resources),'db_connections_max':max((r['db_total'] for r in resources),default=0),
        'db_active_max':max((r['db_active'] for r in resources),default=0),
        'db_lock_waiters_max':max((r['db_waiting_lock'] for r in resources),default=0),
        'movement_connections_max':{port:max((r['movement_connections'][port] for r in resources),default=0) for port in ('9551','9552','9553')},
        'rss_mib_max':{role:round(max(p['rss_mib'] for r in resources for p in r['processes'].values() if p['role']==role),1)
            for role in sorted({p['role'] for r in resources for p in r['processes'].values()})},
        'redis_ops_max':{name:max((r['redis'][name]['instantaneous_ops_per_sec'] for r in resources),default=0) for name in ('movement','service')}}
    http_rows=[row for part in parts for row in part.get('http_requests',[])]
    result['http_started']=http_outcomes(http_rows)
    result['http_by_started']={path:http_outcomes([r for r in http_rows if r['path']==path]) for path in sorted({r['path'] for r in http_rows})}
    result['upstreams']={}
    for line in (OUT/'nginx-logs/native-access.log').read_text().splitlines():
        fields=line.split('|')
        if len(fields)!=7 or not start<=float(fields[0])<=end:continue
        port,code,address=fields[1:4]
        if port in ('8097','8098'):
            key=port+':'+address+':'+code
            result['upstreams'][key]=result['upstreams'].get(key,0)+1
    browser=json.loads((OUT/f'chrome-{mode}.json').read_text())
    result['chrome']={key:value for key,value in browser.items() if key!='metrics'}
    m=browser['metrics']
    result['chrome'].update(frames_ms=percentiles(m['frames']),http_ms=percentiles([r['ms'] for r in m['http']]),
        http_statuses=dict(Counter(str(r['status']) for r in m['http'])),wsOpened=m['wsOpened'],wsClosed=m['wsClosed'],welcome=m['welcome'])
    return result

if __name__=='__main__':
    import sys
    if len(sys.argv)>1:OUT=Path(sys.argv[1]).resolve()
    result={'percentile_method':'nearest rank: ceil(p*N), 1-based',
        'casual':mixed('walk','benchmark-walk.log'),'stress':mixed('real','benchmark-real.log')}
    for name in ('scenario-summary','scenario-checks','scenario-summary-npc','scenario-checks-npc',
            'scenario-summary-poke','scenario-checks-poke','persistence','audit','preflight','question-bank-counts','regression-summary'):
        path=OUT/(name+'.json')
        if path.exists():result[name]=json.loads(path.read_text())
    claims={}
    scenario_http=[]
    for path in sorted(OUT.glob('scenario-samples*.jsonl')):
        for line in path.read_text().splitlines():
            row=json.loads(line)
            if isinstance(row['status'],int) and row['status']>=200:scenario_http.append(row)
            if 'new_awards' not in row:continue
            entry=claims.setdefault(row['action'],{'new':[],'none':[],'new_awards':0})
            entry['new_awards']+=row['new_awards'];entry['new' if row['new_awards'] else 'none'].append(row['ms'])
    from collections import defaultdict
    game_results=defaultdict(Counter)
    for path in OUT.glob('scenario-samples*.jsonl'):
        for line in path.read_text().splitlines():
            row=json.loads(line)
            for field in ('success','game_status'):
                if field in row:game_results[row['action']][str(row[field])]+=1
    result['game_results']={key:dict(value) for key,value in game_results.items()}
    result['scenario_http']={'responses':len(scenario_http),'statuses':dict(Counter(str(r['status']) for r in scenario_http)),
        'server_5xx':sum(500<=r['status']<600 for r in scenario_http)}
    result['award_requests']={key:{'new_awards':v['new_awards'],'new_award_ms':percentiles(v['new']),
        'no_award_ms':percentiles(v['none'])} for key,v in claims.items()}
    if (OUT/'diagnostics.jsonl').exists():
        records=[json.loads(line) for line in (OUT/'diagnostics.jsonl').read_text().splitlines()]
        baseline=[r for r in records if r.get('baseline_probe')]
        trace=(OUT/'diagnostic-mixed.log').read_text()
        diag_start=float(re.search(r'MEASURE_START ([0-9.]+)',trace)[1]);diag_end=float(re.search(r'MEASURE_END ([0-9.]+)',trace)[1])
        records=[v for v in records if v['kind']!='socket_db' or diag_start<=v['at']<=diag_end]
        result['diagnostic_socket_window']=[diag_start,diag_end]
        result['diagnostic_http']={path:{'n':len(rows),'sql_counts':sorted({r['sql_count'] for r in rows}),
            'profile_reads':sorted({r['read_tables'].get('accounts_profile',0) for r in rows}),
            'sql_ms':percentiles([r['sql_ms'] for r in rows]),'handler_ms':percentiles([r['elapsed_ms'] for r in rows])}
            for path in sorted({r['path'] for r in baseline}) for rows in [[r for r in baseline if r['path']==path]]}
        result['diagnostic_socket_db']={fn:{'n':len(rows),'queue_ms':percentiles([r['queue_ms'] for r in rows]),
            'function_ms':percentiles([r['function_ms'] for r in rows])}
            for fn in sorted({r['function'] for r in records if r['kind']=='socket_db'})
            for rows in [[r for r in records if r['kind']=='socket_db' and r['function']==fn and 'queue_ms' in r]]}
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('results.json generated')
