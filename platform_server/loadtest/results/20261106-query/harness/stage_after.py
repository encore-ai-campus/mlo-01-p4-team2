"""Stop the finished before condition, then stage only the validated query patch."""
import hashlib,json,os,shutil,signal,subprocess,time
from pathlib import Path
ROOT=Path('/private/tmp/platform-query-20261106');before=ROOT/'before'
assert (ROOT/'focused-tests-exit.txt').read_text().strip()=='0'
assert 'Finished before' in (ROOT/'before-runner.log').read_text()
rows=json.loads((before/'pids.json').read_text())+json.loads((before/'front-pids.json').read_text())
for row in rows:
 command=subprocess.check_output(['ps','-p',str(row['pid']),'-o','args='],text=True)
 assert any(v in command for v in ('runtime','config.movement_asgi','config.service_asgi','campus_jobs'))
 os.kill(row['pid'],signal.SIGTERM)
pid=int((before/'nginx.pid').read_text())
assert str(before) in subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
os.kill(pid,signal.SIGQUIT)
deadline=time.monotonic()+30
while time.monotonic()<deadline:
 alive=[]
 for row in rows:
  try:os.kill(row['pid'],0);alive.append(row['pid'])
  except ProcessLookupError:pass
 if not alive:break
 time.sleep(.2)
assert not alive,'Before processes must stop before changing the shared source snapshot'
repo=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse');source=Path('/private/tmp/platform-query-source-20261106')
previous=json.loads((ROOT/'before-code-sha256.json').read_text())
changed=['backend/event_story/main.py','backend/event_story/main_views.py','backend/event_story/recipe.py',
 'backend/event_story/game_services.py','backend/event_story/achievement_services.py','backend/learning/services.py']
for name,sha in previous.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==sha,name
for name in changed:shutil.copy2(repo/name,source/name)
after={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source/'backend').rglob('*.py') if '__pycache__' not in p.parts}
assert {p for p in previous if previous[p]!=after[p]}==set(changed)
(ROOT/'after-code-sha256.json').write_text(json.dumps(after,indent=2))
files=changed+['backend/event_story/test_main.py','backend/event_story/test_games.py','backend/tests/test_recipe_story.py','backend/tests/test_query_responses.py']
patch=subprocess.check_output(['git','diff','--',*files[:-1]],cwd=repo)
patch+=subprocess.run(['git','diff','--no-index','--','/dev/null',files[-1]],cwd=repo,stdout=subprocess.PIPE).stdout
(ROOT/'application-change.patch').write_bytes(patch)
print('Before processes stopped. Only six validated query files changed in the runtime source.')
