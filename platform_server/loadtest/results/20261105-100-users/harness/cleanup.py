"""Remove only this run's disposable processes and container."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request

OUT=Path('/private/tmp/platform-load100-20261105')
pids=json.loads((OUT/'server-pids.json').read_text())
for pid in pids:
    command=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
    assert '9055' in command or '8055' in command
    os.kill(pid,signal.SIGTERM)
subprocess.run(['docker','stop','platform-load100-20261105'],check=True,capture_output=True)
time.sleep(1)
ports={}
for port in (8055,9055,55479):
    with socket.socket() as probe:ports[str(port)]=probe.connect_ex(('127.0.0.1',port))!=0
with urllib.request.urlopen('http://127.0.0.1:8004/',timeout=5) as response:live=response.status
report={'test_ports_closed':ports,'live_frontend_http_status':live}
(OUT/'cleanup.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report));assert all(ports.values()) and live==200
