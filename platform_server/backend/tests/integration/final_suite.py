"""Final 100-account integration runner. All infrastructure is owned by this test."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
OUT=Path('/private/tmp/platform-final-integration-20261107')
SNAP=OUT/'source'
EVIDENCE=REPO/'loadtest/results/20261107-final'
PY='/private/tmp/platform-event-postgres-venv/bin/python'
def script(name,*args,timeout=1200,allowed=(0,)):
    log=OUT/'logs'/('run-'+name.replace('.py','')+('-'+'-'.join(args) if args else '')+'.log')
    with log.open('w') as f:
        result=subprocess.run([PY,'-B',str(OUT/'harness'/name),*args],env={**os.environ,'LOCAL44_OUT':str(OUT)},stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    with (OUT/'steps.jsonl').open('a') as f:f.write(json.dumps({'script':name,'args':args,'exit':result.returncode,'at':time.time()})+'\n')
    print(json.dumps({'script':name,'exit':result.returncode,'log':str(log)}),flush=True)
    if result.returncode not in allowed:raise SystemExit(result.returncode)
def prepare():
    assert not OUT.exists() and not EVIDENCE.exists()
    OUT.mkdir();EVIDENCE.mkdir();(OUT/'harness').mkdir();(OUT/'logs').mkdir();SNAP.mkdir()
    shutil.copy2(REPO/'stress-test.md',EVIDENCE/'previous-stress-test.md')
    ignore=shutil.ignore_patterns('.env','.env.*','__pycache__','*.pyc','.venv','node_modules','staticfiles','var','db.sqlite3*')
    for folder in ('backend','frontend'):shutil.copytree(REPO/folder,SNAP/folder,ignore=ignore)
    (SNAP/'loadtest').mkdir();shutil.copy2(REPO/'loadtest/campus_load.py',SNAP/'loadtest/campus_load.py')
    manifest={str(p.relative_to(SNAP)):hashlib.sha256(p.read_bytes()).hexdigest() for p in SNAP.rglob('*') if p.is_file()}
    for dest in (OUT/'source-manifest.json',EVIDENCE/'source-manifest.json'):dest.write_text(json.dumps(manifest,indent=2))
    original=REPO/'loadtest/results/20261107-http128/harness'
    for p in original.glob('*.py'):shutil.copy2(p,OUT/'harness'/p.name)
    text=(REPO/'backend/tests/integration/receipt_stack.py').read_text()
    text=text.replace('/private/tmp/platform-receipt-integration-20261107',str(OUT)).replace(str(REPO),str(SNAP))
    text=text.replace("assert not OUT.exists(),'Refuse to replace an existing test run'","assert not (OUT/'environment.json').exists(),'Refuse to replace an existing test run'")
    text=text.replace("OUT.mkdir();(OUT/'logs').mkdir();(OUT/'static').mkdir()","OUT.mkdir(exist_ok=True);(OUT/'logs').mkdir(exist_ok=True);(OUT/'static').mkdir()")
    text=text.replace("template=REPO/'loadtest/results/20261107-http128/a2'","template=Path("+repr(str(REPO/'loadtest/results/20261107-http128/a2'))+")")
    text=text.replace('receipt-it-','final-it-').replace('receipt_integration_20261107','receipt_final_20261107')
    ports=[(55490,55500),(57531,57541),(57532,57542),(8106,8116),(8107,8117),(8108,8118),(9591,9601),(9592,9602),*zip(range(9651,9655),range(9751,9755)),*zip(range(9661,9665),range(9761,9765))]
    for old,new in [*ports,(9655,9755),(9665,9765)]:text=text.replace(str(old),str(new))
    (OUT/'harness/stack.py').write_text(text)
    for p in (OUT/'harness').glob('*.py'):
        if p.name=='stack.py':continue
        text=p.read_text().replace('/private/tmp/platform-query-source-20261106',str(SNAP))
        for a,b in [('8096','8116'),('55480','55500'),('57521','57541'),('57522','57542'),("'query_'","'receipt_final_'")]:text=text.replace(a,b)
        if p.name=='fixtures.py':
            text=text.replace("username__startswith='load').order_by('username')","username__startswith='final-it-').order_by('username')[:100]")
        if p.name=='scenarios.py':
            text=text.replace("        label=_label or 'main:'+action","        if action=='game_start':data.setdefault('request_uuid',str(uuid.uuid4()))\n        label=_label or 'main:'+action")
        if p.name=='security.py':text=text.replace("ip=json.loads((ROOT/'initial.json').read_text())['private_ip']","ip='127.0.0.1'")
        if p.name=='mixed_extension.py':
            text=text.replace("row['response_status']='rejected' if 'error' in result else 'ok'","row['response_status']='rejected' if 'error' in result else 'ok'\n            if 'error' in result:row['error_message']=str(result['error'])")
            text=text.replace("finish_row(row,'rejected')","finish_row(row,'rejected')\n                if row is not None:row['error_message']=str(message.get('message'))")
            text=text.replace("try:return status,json.loads(body) if body else None","try:\n        parsed=json.loads(body) if body else None\n        if row is not None and isinstance(parsed,dict) and parsed.get('error'):row['error_code']=parsed['error'];row['error_message']=parsed.get('message','')\n        return status,parsed")
        p.write_text(text)
    script('stack.py',timeout=300)
    os.environ.update(json.loads((OUT/'environment.json').read_text()));sys.path.insert(0,str(SNAP/'backend'))
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections
    from learning.models import Question
    for file in ('initial_subject_bank.json','expanded_questions_20261016.json','cs_questions.json'):call_command('seed_learning_questions',file=SNAP/'backend/learning/data'/file,verbosity=0)
    clients=json.loads((OUT/'clients.json').read_text())
    (OUT/'sessions.json').write_text(json.dumps([u['session_key'] for u in clients['users']]))
    (OUT/'metadata.json').write_text(json.dumps({'users':clients['users'],'answers':{str(q.pk):q.correct_answer for q in Question.objects.all()}}))
    for name in ('sessions.json','metadata.json'):(OUT/name).chmod(0o600)
    connections.close_all();print('FINAL_STACK_READY',flush=True)
def backend():
    os.environ.update(json.loads((OUT/'environment.json').read_text()))
    os.environ.update(DJANGO_SETTINGS_MODULE='config.postgres_test_settings',CAMPUS_REDIS_URL='',CAMPUS_MOVEMENT_REDIS_URL='',CAMPUS_SERVICE_REDIS_URL='',CAMPUS_DATA_DIR=str(OUT/'unit-data'))
    Path(os.environ['CAMPUS_DATA_DIR']).mkdir(exist_ok=True)
    sys.path.insert(0,str(SNAP/'backend'));os.chdir(SNAP/'backend')
    import django
    django.setup()
    from django.conf import settings
    from django.test.utils import get_runner
    raise SystemExit(bool(get_runner(settings)(verbosity=1,interactive=False).run_tests([])))
if __name__=='__main__':
    phase=sys.argv[1]
    if phase=='prepare':prepare()
    elif phase=='backend':backend()
    elif phase=='functional':
        script('fixtures.py','reset_scenarios',timeout=300)
        script('scenarios.py',timeout=600,allowed=(0,1))
        script('fixtures.py','audit',timeout=300)
    elif phase in ('walk','real'):
        script('fixtures.py','casual',timeout=300)
        script('benchmark.py',phase,'180',timeout=500,allowed=(0,1))
    elif phase=='security':script('security.py',timeout=500,allowed=(0,1))
    elif phase=='admission':script('admission.py',timeout=500,allowed=(0,1))
    elif phase=='stop':script('stack.py','stop',timeout=90)
