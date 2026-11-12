"""Test runtime only: count real TCP channels and choose repeatable study questions."""
import contextvars
import functools
import hashlib
import sys
import itertools
import json
import os
import queue
import random
import threading
import time
from pathlib import Path
from types import SimpleNamespace

OUT = Path(os.environ['KEEPALIVE_RUN'])
ROLE = os.environ['INTEGRATION_WORKER']
Q = queue.SimpleQueue()
USER = contextvars.ContextVar('study_user')
def emit(row):
    Q.put({'at':time.time(), 'role':ROLE, **row})

def install(backend):
    def writer():
        with (OUT/'trace'/f'{ROLE}.jsonl').open('a', buffering=1) as log:
            while True:
                log.write(json.dumps(Q.get())+'\n')
    threading.Thread(target=writer, daemon=True).start()
    from daphne.http_protocol import WebRequest
    original = WebRequest.process
    identities = itertools.count(1)
    def process(request):
        if not hasattr(request.channel, '_keepalive_connection_id'):
            request.channel._keepalive_connection_id = next(identities)
        emit({'kind':'tcp_request', 'connection_id':request.channel._keepalive_connection_id,
              'path':request.path.decode(errors='replace')})
        return original(request)
    WebRequest.process = process
    from django.conf import settings
    from keepalive_suite import SNAP
    files={}
    for module in list(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name and name.endswith('.py') and Path(name).is_relative_to(SNAP):
            source=Path(name)
            files[str(source.relative_to(SNAP))]=hashlib.sha256(source.read_bytes()).hexdigest()
    attestation={'worker':ROLE,'pid':os.getpid(),'backend':backend,'debug':settings.DEBUG,
                 'files':files,'window_override':backend}
    if backend:
        config=settings.DATABASES['default']
        attestation.update(database=config['NAME'],port=str(config['PORT']),pool=config['OPTIONS']['pool'])
    else:
        attestation.update(destination=settings.BACKEND_URL,keepalive=settings.BACKEND_HTTP_KEEPALIVE,
                           capacity=settings.BACKEND_HTTP_POOL_MAXSIZE,idle_timeout=settings.BACKEND_HTTP_IDLE_TIMEOUT)
    (OUT/('hook-attestation-'+ROLE+'.json')).write_text(json.dumps(attestation))
    if backend:
        from learning import services
        original_start = services.start_learning_session
        @functools.wraps(original_start)
        def start(user, question_type, difficulty, subject='python'):
            token = USER.set(str(user.pk))
            try:
                return original_start(user, question_type, difficulty, subject)
            finally:
                USER.reset(token)
        def sample(pool, count):
            first = pool[0]
            key = ':'.join([USER.get(), first.subject, first.question_type, first.difficulty])
            chosen = random.Random(key).sample(sorted(pool, key=lambda item:str(item.pk)), count)
            emit({'kind':'study_selection', 'user':USER.get(),
                  'subject':first.subject,'type':first.question_type,'difficulty':first.difficulty,
                  'questions':[{'id':str(item.pk),'type':item.question_type,
                                'answer_length':len(str(item.correct_answer))} for item in chosen]})
            return chosen
        services.start_learning_session = start
        # Replace this module reference only; all other games retain their real RNG.
        services.random = SimpleNamespace(sample=sample)
