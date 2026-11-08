"""100 cold-cache download clients against a separate, loopback-only Nginx.

Run with the existing test venv. No login, database writes, or live Nginx edits.
shortcut: HTTP/1.1 on one PC excludes WAN/TLS and browser decoding; test those at deployment.
"""
import asyncio
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import quote, unquote, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'frontend/dist'
OUT = ROOT / 'loadtest/results/20261107-assets100/native-curl-p1'
RUNTIME = Path('/private/tmp/platform-assets100-20261107/native-curl-p1')
NGINX = '/opt/homebrew/bin/nginx'
PARALLEL = 1


def percentile(values, q):
    return round(sorted(values)[max(0, math.ceil(len(values) * q) - 1)], 3) if values else None


def cpu_seconds(value):
    parts = value.split(':')
    return sum(float(v) * 60 ** i for i, v in enumerate(reversed(parts)))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def process_sample(master):
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,time=,rss='], capture_output=True, text=True, check=True)
    rows = [line.split() for line in result.stdout.splitlines()]
    nginx = [r for r in rows if int(r[0]) == master or int(r[1]) == master]
    generator = next(r for r in rows if int(r[0]) == __import__('os').getpid())
    return {'at': time.monotonic(), 'nginx_cpu_seconds': sum(cpu_seconds(r[2]) for r in nginx),
            'nginx_rss_mib': sum(int(r[3]) for r in nginx) / 1024,
            'generator_cpu_seconds': cpu_seconds(generator[2]), 'generator_rss_mib': int(generator[3]) / 1024,
            'nginx_pids': [int(r[0]) for r in nginx]}


async def run_case(label, users, files, base, master):
    rows, clients, probes, resources, lag = [], [], [], [], []
    stop = asyncio.Event()

    async def monitor():
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            while not stop.is_set():
                due = time.monotonic()
                resources.append(await asyncio.to_thread(process_sample, master))
                started = time.monotonic()
                try:
                    r = await client.get(base + '/healthz')
                    probes.append({'ms': (time.monotonic() - started) * 1000, 'status': r.status_code})
                except Exception as exc:
                    probes.append({'ms': (time.monotonic() - started) * 1000, 'error': str(exc)})
                lag.append(max(0, time.monotonic() - due - 0.25) * 1000)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(0.01, due + 0.25 - time.monotonic()))
                except TimeoutError:
                    pass

    config = ''.join('url = "' + base + '/static/campus/' + quote(f['path']) + '"\noutput = "/dev/null"\n' for f in files).encode()
    expected = {f['path']: f['bytes'] for f in files}
    processes = [await asyncio.create_subprocess_exec(
        '/usr/bin/curl', '--parallel', '--parallel-max', str(PARALLEL), '--http1.1',
        '--silent', '--show-error', '--fail', '--max-time', '60', '--connect-timeout', '5',
        '--write-out', '%{http_code}|%{size_download}|%{time_total}|%{url_effective}\n', '--config', '-',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        for _ in range(users)]
    try:
        async def user(uid):
            start_ms = (time.monotonic() - started) * 1000
            stdout, stderr = await processes[uid].communicate(config)
            failures = 0
            for line in stdout.decode().splitlines():
                status, size, elapsed_request, url = line.split('|', 3)
                path = unquote(urlsplit(url).path.removeprefix('/static/campus/'))
                row = {'user': uid, 'file': path, 'status': int(status), 'bytes': int(size),
                       'ms': round(float(elapsed_request) * 1000, 3)}
                if row['status'] != 200 or row['bytes'] != expected[path]:
                    row['error'] = 'status_or_size_mismatch'
                    failures += 1
                rows.append(row)
            clients.append({'user': uid, 'start_ms': start_ms,
                            'seconds': round(time.monotonic() - started, 3), 'failures': failures,
                            'curl_exit_code': processes[uid].returncode, 'stderr': stderr.decode()})

        resources.append(process_sample(master))
        watcher = asyncio.create_task(monitor())
        started = time.monotonic()
        try:
            await asyncio.wait_for(asyncio.gather(*(user(uid) for uid in range(users))), timeout=180)
        finally:
            elapsed = time.monotonic() - started
            stop.set()
            await watcher
            resources.append(process_sample(master))
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
                await process.wait()

    cpu = [(b['nginx_cpu_seconds'] - a['nginx_cpu_seconds']) / (b['at'] - a['at']) * 100
           for a, b in zip(resources, resources[1:]) if b['at'] - a['at'] >= .1]
    success = sum('error' not in r for r in rows)
    result = {'label': label, 'users': users, 'parallel_per_user': PARALLEL,
              'files_per_user': len(files), 'bytes_per_user': sum(f['bytes'] for f in files),
              'requests': len(rows), 'success': success, 'errors': len(rows) - success,
              'completed_users': len(clients), 'successful_users': sum(c['failures'] == 0 for c in clients),
              'user_start_spread_ms': round(max(c['start_ms'] for c in clients) - min(c['start_ms'] for c in clients), 3),
              'downloaded_bytes': sum(r['bytes'] for r in rows), 'elapsed_seconds': round(elapsed, 3),
              'user_seconds_p50': percentile([c['seconds'] for c in clients], .5),
              'user_seconds_p95': percentile([c['seconds'] for c in clients], .95),
              'user_seconds_max': max(c['seconds'] for c in clients),
              'request_ms_p95': percentile([r['ms'] for r in rows], .95),
              'status_counts': dict(Counter(str(r.get('status', 'network_error')) for r in rows)),
              'nginx_cpu_one_core_pct_mean': round((resources[-1]['nginx_cpu_seconds'] - resources[0]['nginx_cpu_seconds']) / (resources[-1]['at'] - resources[0]['at']) * 100, 2),
              'nginx_cpu_one_core_pct_peak': round(max(cpu, default=0), 2),
              'nginx_rss_mib_peak': round(max(r['nginx_rss_mib'] for r in resources), 2),
              'generator_rss_mib_peak': round(max(r['generator_rss_mib'] for r in resources), 2),
              'generator_monitor_overrun_ms_max': round(max(lag, default=0), 2),
              'health_probe_count': len(probes), 'health_probe_errors': sum(p.get('status') != 200 for p in probes),
              'health_probe_ms_p95': percentile([p['ms'] for p in probes], .95)}
    assert len(rows) == users * len(files)
    assert result['downloaded_bytes'] == users * result['bytes_per_user'] or result['errors']
    write_json(OUT / (label + '-requests.json'), rows)
    write_json(OUT / (label + '-clients.json'), clients)
    write_json(OUT / (label + '-resources.json'), resources)
    write_json(OUT / (label + '-health.json'), probes)
    write_json(OUT / (label + '-summary.json'), result)
    print(json.dumps(result), flush=True)
    return result


async def main():
    OUT.mkdir(parents=True, exist_ok=False)
    RUNTIME.mkdir(parents=True, exist_ok=False)
    manifest = [{'path': p.relative_to(ASSETS).as_posix(), 'bytes': p.stat().st_size,
                 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(ASSETS.rglob('*')) if p.is_file()]
    code = [f for f in manifest if Path(f['path']).suffix in {'.js', '.html', '.css'}]
    write_json(OUT / 'manifest.json', manifest)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    conf = f'''daemon off;
worker_processes 1;
pid {RUNTIME}/nginx.pid;
error_log {RUNTIME}/error.log warn;
events {{ worker_connections 8192; }}
http {{
    include /opt/homebrew/etc/nginx/mime.types;
    access_log {RUNTIME}/access.log;
    server {{
        listen 127.0.0.1:{port};
        location = /healthz {{ return 200 "ready"; }}
        location /static/campus/ {{ alias {ASSETS}/; }}
        location / {{ return 404; }}
    }}
}}
'''
    (RUNTIME / 'nginx.conf').write_text(conf)
    (OUT / 'nginx.conf').write_text(conf)
    nginx = subprocess.Popen([NGINX, '-p', str(RUNTIME) + '/', '-c', str(RUNTIME / 'nginx.conf')],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}'
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(50):
                if nginx.poll() is not None:
                    raise RuntimeError(nginx.stderr.read().decode())
                try:
                    if (await client.get(base + '/healthz')).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.1)
            else:
                raise RuntimeError('Nginx did not become ready')
        results = []
        for label, users, files in [('one-user-all', 1, manifest), ('100-users-code', 100, code), ('100-users-all', 100, manifest)]:
            results.append(await run_case(label, users, files, base, nginx.pid))
        write_json(OUT / 'results.json', {'scope': 'isolated local Nginx; HTTP/1.1; no client cache; server filesystem cache retained; no login/WS/DB load',
                                        'base': base, 'results': results})
        assert all(r['errors'] == 0 and r['successful_users'] == r['users'] for r in results)
    finally:
        if nginx.poll() is None:
            nginx.send_signal(signal.SIGQUIT)
            nginx.wait(timeout=10)
        (OUT / 'nginx-error.log').write_text((RUNTIME / 'error.log').read_text())
        write_json(OUT / 'cleanup.json', {'owned_nginx_pid': nginx.pid, 'exit_code': nginx.returncode,
                                        'existing_servers_modified': False})


if __name__ == '__main__':
    assert percentile([1, 2, 3, 4, 5], .95) == 5
    assert percentile([], .95) is None
    assert cpu_seconds('1:02.50') == 62.5
    assert ASSETS.is_dir()
    if '--self-check' in sys.argv:
        print('Percentile, process CPU parsing, and asset directory checks passed.')
    else:
        asyncio.run(main())
