import os
"""Recompute percentiles and censored-request counts from raw measurements."""
from collections import Counter
import json
import math
from pathlib import Path
import re
from expectations import expectation,matches

OUT=Path(os.environ.get('LOCAL44_OUT','/private/tmp/platform-query-20261106/template'))
def percentiles(values):
    values=sorted(values)
    if not values:return None
    pick=lambda q:round(values[max(0,math.ceil(q*len(values))-1)],2)
    return {'n':len(values),'p50':pick(.5),'p95':pick(.95),'p99':pick(.99),'max':round(values[-1],2)}

def request_counts(rows,field='terminal_ms',expected_statuses=(200,201)):
    def kind(status):
        if status in ('ok',101) or isinstance(status,int) and 200<=status<300:return 'success'
        if status=='rejected' or isinstance(status,int) and 400<=status<500:return 'business_rejected'
        if isinstance(status,int) and 500<=status<600:return 'server_error'
        if status=='timeout':return 'timeout'
        if status in ('network_error','transport_error','error'):return 'network_error'
        return 'unresolved'
    groups=Counter(kind(r['status']) for r in rows)
    out={'attempts':len(rows),**{key:groups[key] for key in ('success','business_rejected','server_error','network_error','timeout','unresolved')}}
    out['status_counts']=dict(Counter(str(r['status']) for r in rows))
    out['response_success']=out.pop('success')
    specs=[r.get('expectation') or expectation(r.get('action',''),expected_statuses) for r in rows]
    purposes={s['purpose'] for s in specs}
    assert len(purposes)<=1,'Keep normal processing and intentional validation samples separate'
    out['purpose']=next(iter(purposes),'normal')
    out['success']=sum(kind(r['status'])!='unresolved' and matches(r,s) for r,s in zip(rows,specs))
    out['errors']=len(rows)-out['unresolved']-out['success']
    out['terminal_ms']=percentiles([r[field] for r in rows if field in r and kind(r['status'])!='unresolved'])
    assert out['attempts']==out['success']+out['errors']+out['unresolved']
    assert out['attempts']==sum(groups.values())==sum(out['status_counts'].values())
    assert (out['terminal_ms'] or {}).get('n',0)==out['attempts']-out['unresolved']
    return out

def api_route(row):
    uri=row['uri'].split('?')[0]
    uri=re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',':uuid',uri)
    uri=re.sub(r'(/sessions/)[0-9]+',r'\1:session_id',uri)
    uri=re.sub(r'(/questions/)[0-9]+',r'\1:order',uri)
    return row['method']+' '+uri

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
    result['resources']={'cluster_client_connections_max':max((r['cluster_client_connections'] for r in resources),default=0),'samples':len(resources),'db_connections_max':max((r['db_total'] for r in resources),default=0),
        'db_active_max':max((r['db_active'] for r in resources),default=0),
        'db_lock_waiters_max':max((r['db_waiting_lock'] for r in resources),default=0),
        'movement_connections_max':{port:max((r['movement_connections'][port] for r in resources),default=0) for port in ('9551','9552','9553','9554')},
        'rss_mib_max':{role:round(max(p['rss_mib'] for r in resources for p in r['processes'].values() if p['role']==role),1)
            for role in sorted({p['role'] for r in resources for p in r['processes'].values()})},
        'redis_ops_max':{name:max((r['redis'][name]['instantaneous_ops_per_sec'] for r in resources),default=0) for name in ('movement','service')}}
    http_rows=[row for part in parts for row in part.get('http_requests',[])]
    result['http_started']=http_outcomes(http_rows)
    result['http_started']['client_total_ms']=percentiles([row['total_ms'] for row in http_rows if 'total_ms' in row])
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
    result['chrome'].update(frames_ms=percentiles(m['frames']),http_ms=percentiles([r['ms'] for r in m['http'] if 'ms' in r]),
        http_statuses=dict(Counter(str(r['status']) for r in m['http'])),wsOpened=m['wsOpened'],wsClosed=m['wsClosed'],welcome=m['welcome'])
    result['http_terminal']=request_counts(http_rows)
    result['api_requests']={route:request_counts([r for r in http_rows if api_route(r)==route]) for route in sorted({api_route(r) for r in http_rows})}
    result['control_terminal']={action:request_counts([r for r in rows if r['action']==action],expected_statuses=('ok',)) for action in sorted({r['action'] for r in rows})}
    result['chat_terminal']=request_counts([r for part in parts for r in part['chat_requests']],expected_statuses=('ok',))
    result['drain_reports']=[r for part in parts for r in part['drain_reports']]
    result['chrome']['http_terminal']=request_counts(m['http'],'ms')
    chrome_rows=[{**r,'uri':r['path'],'terminal_ms':r['ms']} if 'ms' in r else {**r,'uri':r['path']} for r in m['http']]
    combined=http_rows+chrome_rows
    result['combined_http_terminal']=request_counts(combined)
    result['combined_api_requests']={route:request_counts([r for r in combined if api_route(r)==route]) for route in sorted({api_route(r) for r in combined})}
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
    if (OUT/'admission.json').exists():
        admission=json.loads((OUT/'admission.json').read_text())
        result['admission']={'attempts':len(admission),'statuses':dict(Counter(str(v['status']) for v in admission)),
            'welcomed':sum(v['welcome'] for v in admission),'response_ms':percentiles([v['ms'] for v in admission]),'welcome_ms':percentiles([v['ms'] for v in admission if v['welcome']]),
            'rounds':{str(i):dict(Counter(str(v['status']) for v in admission if v['trial']==i)) for i in range(10)}}
        result['admission']['requests']=request_counts([{**r,'status':'timeout' if r.get('error')=='TimeoutError' else r['status']} for r in admission],'ms',expected_statuses=(101,))
    claims={}
    scenario_http=[]
    scenario_all=[]
    for path in sorted(OUT.glob('scenario-samples*.jsonl')):
        for line in path.read_text().splitlines():
            row=json.loads(line)
            scenario_all.append(row)
            if row.get('method') in ('GET','POST') and isinstance(row['status'],int) and row['status']>=200:scenario_http.append(row)
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
    result['scenario_requests']={action:request_counts([r for r in scenario_all if r['action']==action],'ms')
        for action in sorted({r['action'] for r in scenario_all})}
    result['scenario_routes']={action:sorted({api_route(r) for r in scenario_all if r['action']==action and 'uri' in r})
        for action in result['scenario_requests']}
    result['normal_scenario_requests']={k:v for k,v in result['scenario_requests'].items() if v['purpose']=='normal'}
    result['validation_requests']={k:v for k,v in result['scenario_requests'].items() if v['purpose']=='validation'}
    result['scenario_not_reached']={action:100-len({r['account_index'] for r in scenario_all if r['action']==action})
        for action in result['scenario_requests']}
    resources=[json.loads(line) for line in (OUT/'resources.jsonl').read_text().splitlines()]
    result['all_phases_connections']={'cluster_max':max(r['cluster_client_connections'] for r in resources),'by_worker_max':{name:max(r['connections_by_worker'].get(name,0) for r in resources) for name in sorted({n for r in resources for n in r['connections_by_worker']})}}
    snapshots=[json.loads(line) for p in OUT.glob('pool-stats-*.jsonl') for line in p.read_text().splitlines()]
    result['pool_stats']={name:{'configured_max':max((r['stats'].get('pool_max',0) for r in snapshots if r['worker']==name and r['stats']),default=0),
        'pool_size_max':max((r['stats'].get('pool_size',0) for r in snapshots if r['worker']==name and r['stats']),default=0),
        'waiting_max':max((r['stats'].get('requests_waiting',0) for r in snapshots if r['worker']==name and r['stats']),default=0),
        'queued_requests_counter_max':max((r['stats'].get('requests_queued',0) for r in snapshots if r['worker']==name and r['stats']),default=0),
        'wait_ms_counter_max':max((r['stats'].get('requests_wait_ms',0) for r in snapshots if r['worker']==name and r['stats']),default=0),
        'requests_errors_max':max((r['stats'].get('requests_errors',0) for r in snapshots if r['worker']==name and r['stats']),default=0)} for name in sorted({r['worker'] for r in snapshots})}
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('results.json generated')
