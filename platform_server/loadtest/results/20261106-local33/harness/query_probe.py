"""Sequential diagnostic HTTP probes, separate from primary 100-user results."""
import json
from pathlib import Path
import urllib.request
OUT=Path('/private/tmp/platform-local33-20261106')
keys=json.loads((OUT/'sessions.json').read_text())
for i in range(10):
    for path in ('/api/event-main/status/','/api/event-main/ranking/','/api/event-story/achievements/','/api/event-story/recipe/status/'):
        request=urllib.request.Request('http://127.0.0.1:9561'+path,headers={'Cookie':'REDACTED'+keys[i],'X-Load-Probe':'query-count'})
        with urllib.request.urlopen(request,timeout=20) as response:
            assert response.status==200;response.read()
print('40 sequential diagnostic reads completed')
