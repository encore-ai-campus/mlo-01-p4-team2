"""Restart only the isolated apps, never the live 8004/9003 services."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

OUT=Path('/private/tmp/platform-load100-20261105')
ROOT=Path('/private/tmp/platform-event-pr-53')
def restart(diagnostic=False):
    env=json.loads((OUT/'environment.json').read_text())
    pids=json.loads((OUT/'server-pids.json').read_text())
    for pid in pids:
        command=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
        assert '9055' in command or '8055' in command
        os.kill(pid,signal.SIGTERM)
    for _ in range(50):
        busy=False
        for port in (8055,9055):
            with socket.socket() as probe:
                busy=busy or probe.connect_ex(('127.0.0.1',port))==0
        if not busy:break
        time.sleep(.2)
    else:
        for pid in pids:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
        time.sleep(1)
    new=[]
    for directory,args,name in [('backend',['run-campus.py','--port','9055','--bind','127.0.0.1','--noreload'],'backend'),
        ('frontend',['manage.py','runserver','127.0.0.1:8055','--noreload'],'frontend')]:
        if diagnostic and directory=='backend':args[0]=str(Path(__file__).with_name('diagnostics.py'))
        process=subprocess.Popen([sys.executable,'-B',*args],cwd=ROOT/directory,env=env,
            stdout=(OUT/f'{name}-restart.log').open('a'),stderr=subprocess.STDOUT,start_new_session=True)
        new.append(process.pid)
    (OUT/'server-pids.json').write_text(json.dumps(new))
    time.sleep(3)
    print('isolated apps restarted:',new)
if __name__=='__main__':restart('--diagnostic' in sys.argv)
