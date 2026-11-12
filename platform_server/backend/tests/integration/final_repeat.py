import importlib.util,json,os,sys
from pathlib import Path
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
ROOT=Path('/private/tmp/platform-final-integration-20261107-r2')
spec=importlib.util.spec_from_file_location('driver',REPO/'backend/tests/integration/final_suite.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
driver.OUT=ROOT;driver.SNAP=ROOT/'source';driver.EVIDENCE=REPO/'loadtest/results/20261107-final-r2'
def main():
    phase=sys.argv[1]
    if phase=='prepare':
     driver.script('stack.py',timeout=300)
     os.environ.update(json.loads((ROOT/'environment.json').read_text()));sys.path.insert(0,str(ROOT/'source/backend'))
     import django
     django.setup()
     from django.core.management import call_command
     from django.db import connections
     from learning.models import Question
     for f in ('initial_subject_bank.json','expanded_questions_20261016.json','cs_questions.json'):call_command('seed_learning_questions',file=ROOT/'source/backend/learning/data'/f,verbosity=0)
     clients=json.loads((ROOT/'clients.json').read_text())
     (ROOT/'sessions.json').write_text(json.dumps([u['session_key'] for u in clients['users']]))
     (ROOT/'metadata.json').write_text(json.dumps({'users':clients['users'],'answers':{str(q.pk):q.correct_answer for q in Question.objects.all()}}))
     for f in ('sessions.json','metadata.json'):(ROOT/f).chmod(0o600)
     connections.close_all();print('R2_READY',flush=True)
    elif phase=='backend':driver.backend()
    elif phase=='frontend':
     import subprocess
     with (ROOT/'logs/frontend.log').open('w') as out:
      r=subprocess.run(['node','--test',*map(str,sorted((ROOT/'source/frontend/tests').glob('*.test.cjs')))],cwd=ROOT/'source',stdout=out,stderr=subprocess.STDOUT)
     print('FRONTEND_EXIT',r.returncode);raise SystemExit(r.returncode)
    elif phase=='functional':
     driver.script('fixtures.py','reset_scenarios',timeout=300)
     driver.script('scenarios.py',timeout=600,allowed=(0,1))
     driver.script('fixtures.py','audit',timeout=300)
    elif phase in ('walk','real'):
     driver.script('fixtures.py','casual',timeout=300)
     driver.script('benchmark.py',phase,'180',timeout=500,allowed=(0,1))
    elif phase in ('receipt_protocol','receipt_browser','assets','security','admission'):driver.script(phase+'.py',timeout=600,allowed=(0,1))
    elif phase=='stop':driver.script('stack.py','stop',timeout=90)

if __name__=='__main__':main()
