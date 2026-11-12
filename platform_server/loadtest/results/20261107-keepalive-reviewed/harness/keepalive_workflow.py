"""Run only approved cases, drain each condition and always release owned resources."""
import json
import subprocess
import sys
import shutil
import time
from keepalive_suite import ROOT,REPO,PY,VARIANTS,PG,REDIS,gate,prepare,start,run,stop

def require(label,name,*args):
    code=run(label,name,*args)
    if code:raise RuntimeError('Test preparation failed: '+name+' '+str(args))
def main():
    gate('load')
    resume_extras='--resume-a1-extras' in sys.argv
    if resume_extras:
        assert (ROOT/'started-pg.json').exists() and json.loads((ROOT/'owned-redis.json').read_text())==REDIS
        steps=[json.loads(line) for line in (ROOT/'a1/steps.jsonl').read_text().splitlines()]
        assert any(r['name']=='receipt_protocol' and r['exit']==0 for r in steps)
        assert any(r['name']=='receipt_browser' and r['exit']==1 for r in steps)
        assert all(not (ROOT/label/'steps.jsonl').exists() for label in ('b1','b2','a2'))
        subprocess.run(['docker','start',PG,*REDIS],check=True,stdout=subprocess.DEVNULL)
    elif '--prepared' in sys.argv:
        assert (ROOT/'started-pg.json').exists()
        assert json.loads((ROOT/'owned-redis.json').read_text())==REDIS
        assert all((ROOT/label/'environment.json').exists() and
                   not (ROOT/label/'steps.jsonl').exists() and not (ROOT/label/'processes.json').exists()
                   for label in VARIANTS),'Only unused prepared clones can resume'
        subprocess.run(['docker','start',PG,*REDIS],check=True,stdout=subprocess.DEVNULL)
    else:
        prepare()
    if not resume_extras:
        subprocess.run([PY,'-B',str(REPO/'backend/tests/integration/keepalive_nginx.py')],check=True)
    for label in VARIANTS:
        try:
            if resume_extras and label=='a1':
                archive=ROOT/(label+'-before-browser-resume')
                if archive.exists():archive=ROOT/(label+'-before-browser-resume-'+str(time.time_ns()))
                shutil.copytree(ROOT/label,archive,symlinks=True,ignore=shutil.ignore_patterns('chrome-profile'))
            start(label)
            if not (resume_extras and label=='a1'):
                require(label,'fixtures','reset_scenarios')
                require(label,'owned_fixture','learning_baseline')
                # Unexpected responses remain in raw evidence and do not suppress independent checks.
                run(label,'scenarios')
                require(label,'fixtures','audit')
                require(label,'owned_fixture','audit')
                if label in ('a1','b1'):
                    for mode in ('walk','real'):
                        require(label,'fixtures','casual')
                        require(label,'benchmark',mode,'180')
                    require(label,'owned_fixture','receipt_reset')
                    require(label,'owned_fixture','tickets')
                    require(label,'receipt_protocol')
                    receipt=json.loads((ROOT/label/'results.json').read_text())
                    assert receipt['outcome']=='passed',receipt.get('failure')
            if label in ('a1','b1'):
                require(label,'owned_fixture','browser_reset')
                require(label,'receipt_browser')
                require(label,'admission')
                require(label,'security')
        finally:
            stop(label)
    subprocess.run([PY,'-B',str(REPO/'backend/tests/integration/keepalive_collect.py')],check=True)

if __name__=='__main__':
    try:
        main()
    finally:
        subprocess.run([PY,'-B',str(REPO/'backend/tests/integration/keepalive_suite.py'),'cleanup'],check=True)
