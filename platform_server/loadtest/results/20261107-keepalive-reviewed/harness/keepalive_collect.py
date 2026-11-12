"""Raw accounting only: expected refusals never enter the unexpected-error count."""
import json
import math
from collections import Counter
from pathlib import Path
from keepalive_suite import ROOT,VARIANTS

EXPECTED_CONTROL={
    ('jump','착지한 뒤 다시 점프해 주세요.'),
    ('jump','현재 공간에서 다시 점프해 주세요.'),
    ('open_booth','부스 중앙의 E 표시 가까이로 이동해 주세요.'),
    ('interact','학습 영역이나 출입구 가까이에서 이용해 주세요.'),
}
def percentiles(values):
    values=sorted(values)
    if not values:return {'n':0,'p95':None,'p99':None}
    return {'n':len(values),'p95':round(values[math.ceil(.95*len(values))-1],2),
            'p99':round(values[math.ceil(.99*len(values))-1],2)}
def terminal(row,mode):
    status=row.get('status')
    if mode=='scenario':
        if not row['expected']:return 'error'
        spec=row['expectation']
        if spec['purpose']=='validation' or row.get('game_status')=='INTERRUPTED':return 'normal'
        return 'success'
    if mode=='receipt':
        return {'success':'success','expected_rejection':'normal','expected_fault':'normal'}.get(row['outcome'],'error')
    if mode=='security':
        if not row['passed']:return 'error'
        return 'normal' if row['expected'] in (403,4401) else 'success'
    if mode=='chat':
        if status=='ok':return 'success'
        if status=='rejected' and row.get('error_message')=='잠시 후 다시 보내 주세요.':return 'normal'
        return 'error'
    if mode=='control':
        if status=='ok':return 'success'
        if status=='rejected' and (row.get('action'),row.get('error_message')) in EXPECTED_CONTROL:return 'normal'
        return 'error'
    return 'success' if isinstance(status,int) and 200<=status<300 else 'error'
def summarize(rows,mode='http'):
    counts=Counter(terminal(row,mode) for row in rows)
    def no_response(row):
        return (row.get('status') in ('timeout','network_error','transport_error','error','unresolved','pending',None)
                or mode=='receipt' and (not row.get('complete') or row.get('outcome')=='no_response'))
    unanswered=sum(no_response(row) for row in rows)
    values=[row.get('terminal_ms',row.get('ms')) for row in rows if not no_response(row)]
    assert all(isinstance(v,(int,float)) for v in values),'Response missing its full-body latency'
    complete=percentiles(values)
    result={'requests':len(rows),'success':counts['success'],'normal_handling':counts['normal'],
            'errors':counts['error'],'unanswered':unanswered,
            'p95':None if unanswered else complete['p95'],'p99':None if unanswered else complete['p99'],
            'received_response_latency':complete,'statuses':dict(Counter(str(r.get('status')) for r in rows))}
    assert result['requests']==result['success']+result['normal_handling']+result['errors']
    assert result['unanswered']<=result['errors']
    return result
def jsonlines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
def read(path):return json.loads(path.read_text())
def main():
    result={'variants':{},'comparison_valid':True}
    selections={}
    all_scenario={'a':[],'b':[]}
    for label in VARIANTS:
        out=ROOT/label
        if not (out/'scenario-samples.jsonl').exists():continue
        rows=jsonlines(out/'scenario-samples.jsonl')
        checks=read(out/'scenario-checks.json')
        steps=jsonlines(out/'steps.jsonl')
        functional=next(row for row in steps if row['name']=='scenarios')
        trace=[row for f in (out/'trace').glob('*.jsonl') for row in jsonlines(f)]
        chosen=[row for row in trace if row['kind']=='study_selection' and functional['started']<=row['at']<=functional['ended']]
        selections[label]={row['user']:{key:row[key] for key in ('role','subject','type','difficulty','questions')} for row in chosen}
        assert len(chosen)==len(selections[label]),'Repeated study selections within the same full scenario'
        tcp=[row for row in trace if row['kind']=='tcp_request' and functional['started']<=row['at']<=functional['ended']]
        phases={}
        for mode in ('walk','real'):
            files=sorted(out.glob(f'raw-{mode}-*.json'))
            if not files:continue
            parts=[read(f) for f in files]
            http=[r for part in parts for r in part['http_requests']]
            browser=read(out/f'chrome-{mode}.json')
            browser_http=[{**r,'terminal_ms':r.get('ms')} for r in browser['metrics']['http']]
            control=[r for part in parts for r in part['control_requests']]
            chat=[r for part in parts for r in part['chat_requests']]
            phases[mode]={'http':summarize(http+browser_http),'control':summarize(control,'control'),
                'chat':summarize(chat,'chat'),'drain':[r for part in parts for r in part['drain_reports']],
                'browser_js_errors':browser['js_errors'],'browser_ws_closed':browser['metrics']['wsClosed'],
                'movement_p95_p99':percentiles([x*1000 for part in parts for x in part.get('move_lag',[])]),
                'state_gap_p95_p99':percentiles([x*1000 for part in parts for x in part.get('state_gap',[])])}
        counts={}
        for role in sorted({row['role'] for row in tcp}):
            group=[row for row in tcp if row['role']==role]
            counts[role]={'requests':len(group),'tcp_connections':len({r['connection_id'] for r in group})}
        item={'functional':summarize(rows,'scenario'),'actions':{action:summarize([r for r in rows if r['action']==action],'scenario') for action in sorted({r['action'] for r in rows})},
            'account_checks':checks,'http_connections':counts,'study_selections':len(chosen),
            'study_audit':read(out/'learning-audit.json'),'event_audit':read(out/'audit.json'),'mixed':phases}
        if (out/'raw-samples.jsonl').exists():item['receipt']=summarize(jsonlines(out/'raw-samples.jsonl'),'receipt')
        if (out/'results.json').exists():item['receipt_checks']=read(out/'results.json')
        if (out/'browser-result.json').exists():item['browser_receipt']=read(out/'browser-result.json')
        if (out/'security.json').exists():item['security']=summarize(read(out/'security.json'),'security')
        if (out/'admission.json').exists():
            admission=[{**r,'passed':r['status']==101 and r['welcome'],'expected':101} for r in read(out/'admission.json')]
            item['admission']=summarize(admission,'security')
        access=[line.split('|') for line in (out/'logs/nginx-access.log').read_text().splitlines()]
        item['entry_5xx_status_counts']=dict(Counter(row[2] for row in access if len(row)>=7 and row[1]=='8116' and row[2] in ('502','504')))
        result['variants'][label]=item
        all_scenario[label[0]].extend(rows)
    if len(selections)==4:
        reference=selections['a1']
        result['comparison_valid']=len(reference)==100 and all(value==reference for value in selections.values())
        result['identical_question_order_and_worker_affinity']=result['comparison_valid']
        if not result['comparison_valid']:
            result['comparison_block_reason']='Study selection or service-worker affinity differs; do not claim causal A/B latency delta'
    else:
        result['comparison_valid']=False
        result['comparison_block_reason']='Four full scenario runs are required'
    result['combined_functional']={label:summarize(rows,'scenario') for label,rows in all_scenario.items()}
    (ROOT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'comparison_valid':result['comparison_valid'],'variants':list(result['variants'])}),flush=True)

if __name__=='__main__':main()
