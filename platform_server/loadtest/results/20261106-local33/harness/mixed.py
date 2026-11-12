"""Reuse the repository's protocol simulator against the isolated server only."""
import multiprocessing
import os
from pathlib import Path
import sys

ROOT = Path('/private/tmp/platform-local33-source-20261106')
OUT = Path('/private/tmp/platform-local33-20261106')
if __name__ == '__main__':
    multiprocessing.set_start_method('fork')
    env = __import__('json').loads((OUT / 'environment.json').read_text())
    os.environ.update(env, CAMPUS_HOST='127.0.0.1', CAMPUS_PORT='8096')
    sys.path.insert(0, str(ROOT / 'backend'))
    source = (ROOT / 'loadtest/campus_load.py').read_text()
    source = source.replace('"/data/sessions.json"', repr(str(OUT / 'sessions.json')))
    source = source.replace('"/app"', repr(str(ROOT / 'backend')))
    source = source.replace("/ws/campus/", "/ws/movement/")
    source = source.replace('print("MEASURE_START", flush=True)', 'print("MEASURE_START", time.time(), flush=True)')
    source = source.replace('print("MEASURE_END", flush=True)', 'print("MEASURE_END", time.time(), flush=True)')
    # Keep the existing generator and its measurements; only container paths change.
    context = {'__name__': 'isolated_load'}
    exec(compile(source, str(ROOT / 'loadtest/campus_load.py'), 'exec'), context)
    extension = Path(__file__).with_name('mixed_extension.py').read_text()
    exec(compile(extension, 'mixed_extension.py', 'exec'), context)
    context['main']()
