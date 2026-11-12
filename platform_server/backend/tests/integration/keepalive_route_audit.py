"""Offline audit: distinguish fixed initial choices from timeout-driven worker reassignment."""
import json
import re
import shutil
from collections import Counter,defaultdict
from pathlib import Path
from keepalive_suite import ROOT,VARIANTS
from keepalive_collect import jsonlines

SESSION=re.compile(r'^/api/learning/sessions/(\d+)(?:/|$)')
def audit(label):
    out=ROOT/label
    step=next(r for r in jsonlines(out/'steps.jsonl') if r['name']=='scenarios')
    trace=[row for f in sorted((out/'trace').glob('*.jsonl')) for row in jsonlines(f)
           if step['started']<=row['at']<=step['ended']]
    samples=jsonlines(out/'scenario-samples.jsonl')
    clients=json.loads((out/'clients.json').read_text())['users']
    indices={row['user_id']:index for index,row in enumerate(clients)}
    initial={indices[row['user']]:row['role'] for row in trace if row['kind']=='study_selection'}
    owners={}
    for row in samples:
        match=SESSION.match(row.get('uri',''))
        if match:
            key=match.group(1)
            assert key not in owners or owners[key]==row['account_index']
            owners[key]=row['account_index']
    seen=defaultdict(set)
    mismatches=Counter()
    rerouted=set()
    served=0
    tcp=[r for r in trace if r['kind']=='tcp_request']
    for row in tcp:
        if not row['role'].startswith('service-'):continue
        match=SESSION.match(row['path'])
        if not match:continue
        index=owners[match.group(1)]
        served+=1
        seen[index].update((initial[index],row['role']))
        if row['role']!=initial[index]:
            mismatches[initial[index]+'->'+row['role']]+=1
            rerouted.add(index)
    hops={}
    for prefix,hop in [('front-','entry_to_front'),('service-','internal_to_service')]:
        rows=[r for r in tcp if r['role'].startswith(prefix)]
        hops[hop]={'requests':len(rows),'tcp_connections':len({(r['role'],r['connection_id']) for r in rows})}
    internal=[]
    for line in (out/'logs/nginx-access.log').read_text().splitlines():
        row=line.split('|')
        if len(row)>=11 and row[1]=='8118' and step['started']<=float(row[0])<=step['ended']:
            internal.append(row)
    hops['front_to_internal']={'requests':len(internal),'tcp_connections':len({row[9] for row in internal})}
    files=[out/'db-monitor.jsonl']
    if label=='a1':
        files.append(ROOT/'a1-before-browser-resume/db-monitor.jsonl')
    monitor=[row for f in files for row in jsonlines(f)]
    return {'initial_accounts':len(initial),'served_study_uri_requests':served,
            'reassigned_accounts':len(rerouted),'reassigned_requests':sum(mismatches.values()),
            'worker_transitions':dict(mismatches),'multiple_worker_accounts':sum(len(roles)>1 for roles in seen.values()),
            'http_hops':hops,'database_client_peak':max(r['cluster_connections'] for r in monitor),
            'lock_wait_peak':max(r['lock_waiting'] for r in monitor)}

def main():
    result={label:audit(label) for label in VARIANTS}
    destination=ROOT/'route-audit.json'
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
    original=ROOT/'initial-conditions-summary.json'
    if not original.exists():shutil.copy2(ROOT/'summary.json',original)
    summary=json.loads(original.read_text())
    summary['initial_conditions_match']=summary['comparison_valid']
    summary['identical_question_order_and_initial_worker']=summary.pop('identical_question_order_and_worker_affinity',False)
    summary['actual_study_worker_affinity_match']=all(row['reassigned_accounts']==0 for row in result.values())
    summary['comparison_valid']=summary['initial_conditions_match'] and summary['actual_study_worker_affinity_match']
    if not summary['comparison_valid']:
        summary['comparison_block_reason']='Actual study-worker routing differs; compare observed outcomes, not an isolated processing-speed uplift.'
    summary['route_audit']=result
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({'initial_conditions_match':summary['initial_conditions_match'],
        'isolated_latency_comparison_valid':summary['comparison_valid'],
        'reassigned_accounts':{key:row['reassigned_accounts'] for key,row in result.items()}}),flush=True)

if __name__=='__main__':main()
