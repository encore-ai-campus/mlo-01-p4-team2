import os
"""99 protocol clients plus one actual headless Chrome profile = 100 accounts."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from chrome import Chrome,OUT

mode=sys.argv[1]
duration=sys.argv[2] if len(sys.argv)>2 else '180'
assert mode in ('walk','real')
browser=Chrome()
log=(OUT/f'benchmark-{mode}.log').open('w')
try:
    browser.screenshot(f'chrome-{mode}-before.png')
    arguments=[sys.executable,'-B',str(Path(__file__).with_name('mixed.py')),'--users','99','--behavior',mode,
        '--moving','0.2','--client','new','--procs','2','--ramp','15','--warmup','15','--measure',duration]
    process=subprocess.Popen(arguments,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    lines=queue.Queue()
    def read():
        for line in process.stdout:lines.put(line)
    threading.Thread(target=read,daemon=True).start()
    measuring=False;ended=False;next_key=time.monotonic();release=None;next_probe=0
    while process.poll() is None or not lines.empty():
        while not lines.empty():
            line=lines.get();log.write(line);log.flush()
            if line.startswith('MEASURE_START'):
                browser.start();measuring=True;next_probe=time.monotonic()+20
            elif line.startswith('MEASURE_END'):
                measuring=False;ended=True;browser.key(False);browser.stop()
        now=time.monotonic()
        if not ended and now>=next_key:
            browser.key(True);release=now+.15;next_key=now+1.2
        if release is not None and now>=release:browser.key(False);release=None
        if measuring and now>=next_probe:
            # These are real same-origin Chrome fetches, separately labelled from UI-triggered requests.
            browser.evaluate("fetch('/api/event-story/achievements/').catch(()=>{})")
            next_probe=now+20
        time.sleep(.03)
    assert process.wait()==0
    result=browser.end();result.update(protocol_users=99,chrome_users=1,total_accounts=100)
    (OUT/f'chrome-{mode}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    browser.screenshot(f'chrome-{mode}-after.png')
    result=json.loads((OUT/f'chrome-{mode}.json').read_text())
    assert result['metrics']['welcome'] and len(result['metrics']['frames'])>100
    assert not result['metrics']['wsClosed'],result['metrics']['wsClosed']
    print(f'{mode}: 99 bots + one real Chrome account completed')
finally:
    if 'process' in globals() and process.poll() is None:process.terminate();process.wait(timeout=10)
    browser.close();log.close()
