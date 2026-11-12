"""Recompute percentiles and censored-request counts from raw measurements."""
from collections import Counter
import json
import math
from pathlib import Path
import re

OUT=Path('/private/tmp/platform-load100-20261105')
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
    result['resources']={'samples':len(resources),'db_connections_max':max((r['db_connections'] for r in resources),default=0),
        'db_active_max':max((r['db_active'] for r in resources),default=0),'processes':{}}
    for index,name in enumerate(('backend','frontend')):
        cpu=[];rss=[]
        for row in resources:
            fields=row['processes'][index].split();cpu.append(float(fields[1]));rss.append(int(fields[2])/1024)
        result['resources']['processes'][name]={'cpu_percent':percentiles(cpu),'rss_mib_max':round(max(rss),1) if rss else None}
    return result

if __name__=='__main__':
    result={'percentile_method':'nearest rank: ceil(p*N), 1-based',
        'stress':mixed('real','stress100-final.log'),'casual':mixed('walk','casual100-final.log'),
        'scenarios':json.loads((OUT/'scenario-summary.json').read_text()),
        'checks':json.loads((OUT/'scenario-checks.json').read_text()),
        'npc':json.loads((OUT/'scenario-summary-npc.json').read_text()),
        'npc_checks':json.loads((OUT/'scenario-checks-npc.json').read_text()),
        'audit':json.loads((OUT/'audit.json').read_text()),
        'regression':json.loads((OUT/'regression-result.json').read_text()),
        'persistence':json.loads((OUT/'persistence.json').read_text()),
        'hardware':json.loads((OUT/'hardware.json').read_text()),
        'live_db':json.loads((OUT/'live-db-config.json').read_text()),
        'test_db':json.loads((OUT/'test-db-config.json').read_text()),
        'bank_counts':json.loads((OUT/'question-bank-counts.json').read_text())}
    claims={}
    for name in ('scenario-samples.jsonl','scenario-samples-npc.jsonl','scenario-samples-poke.jsonl'):
        if not (OUT/name).exists():continue
        for line in (OUT/name).read_text().splitlines():
            row=json.loads(line)
            if 'new_awards' not in row:continue
            entry=claims.setdefault(row['action'],{'new_award_ms':[],'no_award_ms':[],'new_awards':0})
            entry['new_awards']+=row['new_awards']
            entry['new_award_ms' if row['new_awards'] else 'no_award_ms'].append(row['ms'])
    result['award_requests']={key:{'new_awards':value['new_awards'],'new_award_ms':percentiles(value['new_award_ms']),
        'no_award_ms':percentiles(value['no_award_ms'])} for key,value in claims.items()}
    if (OUT/'scenario-summary-poke.json').exists():
        result['private_poke']=json.loads((OUT/'scenario-summary-poke.json').read_text())
        result['private_poke_checks']=json.loads((OUT/'scenario-checks-poke.json').read_text())
    diagnostic=[json.loads(line) for line in (OUT/'diagnostics.jsonl').read_text().splitlines()]
    baseline=[row for row in diagnostic if row['kind']=='http'][:40]
    expected=['/api/event-main/status/','/api/event-main/ranking/','/api/event-story/achievements/','/api/event-story/recipe/status/']*10
    assert [row['path'] for row in baseline]==expected,'baseline query probe order changed'
    result['diagnostic_http']={}
    for path in sorted(set(expected)):
        rows=[row for row in baseline if row['path']==path]
        result['diagnostic_http'][path]={'n':len(rows),'sql_counts':sorted(set(row['sql_count'] for row in rows)),
            'sql_ms':percentiles([row['sql_ms'] for row in rows]),'handler_ms':percentiles([row['elapsed_ms'] for row in rows]),
            'profile_reads':sorted(set(row['read_tables'].get('accounts_profile',0) for row in rows))}
    result['diagnostic_socket_db']={}
    for fn in sorted({row['function'] for row in diagnostic if row['kind']=='socket_db'}):
        rows=[row for row in diagnostic if row['kind']=='socket_db' and row['function']==fn and 'queue_ms' in row and 'function_ms' in row]
        result['diagnostic_socket_db'][fn]={'n':len(rows),'queue_ms':percentiles([row['queue_ms'] for row in rows]),
            'function_ms':percentiles([row['function_ms'] for row in rows]),'sql_ms':percentiles([row['sql_ms'] for row in rows])}
    assert len(result['bank_counts'])==18 and all(row['n']==200 for row in result['bank_counts'])
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('results.json generated')
