"""Check the loading gate in a dedicated Chrome and isolated local frontend/Nginx.

Renders the real template with a mock account/API; never touches the shared DB.
"""
import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'loadtest/results/20261107-loading-ui'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Browser:
    def __init__(self):
        self.profile = Path(tempfile.mkdtemp(prefix='campus-loading-chrome-'))
        self.log = (OUT / 'chrome.log').open('w')
        self.process = subprocess.Popen([
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--headless=new',
            '--no-first-run', '--no-default-browser-check', '--disable-sync', '--remote-debugging-port=0',
            '--window-size=1440,1000', '--user-data-dir=' + str(self.profile), 'about:blank'],
            stdout=self.log, stderr=self.log)
        for _ in range(100):
            if (self.profile / 'DevToolsActivePort').exists():
                break
            time.sleep(.1)
        port = int((self.profile / 'DevToolsActivePort').read_text().splitlines()[0])
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as response:
            tab = next(t for t in json.load(response) if t['type'] == 'page')
        self.socket = connect(tab['webSocketDebuggerUrl'], proxy=None, open_timeout=5, max_size=2**24, legacy=True)
        self.sequence = 0
        self.events = []
        self.command('Page.enable')
        self.command('Runtime.enable')
        self.command('Network.enable')
        self.command('Network.setCacheDisabled', {'cacheDisabled': True})

    def command(self, method, params=None):
        self.sequence += 1
        current = self.sequence
        self.socket.send(json.dumps({'id': current, 'method': method, 'params': params or {}}))
        while True:
            result = json.loads(self.socket.recv(timeout=30))
            if 'method' in result:
                self.events.append(result)
            if result.get('id') == current:
                if 'error' in result:
                    raise RuntimeError(str(result['error']))
                return result.get('result', {})

    def evaluate(self, expression):
        result = self.command('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
        if 'exceptionDetails' in result:
            raise RuntimeError(str(result['exceptionDetails']))
        return result['result'].get('value')

    def wait(self, expression, seconds=40):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.evaluate('Boolean(' + expression + ')'):
                return
            time.sleep(.1)
        raise TimeoutError(expression)

    def screenshot(self, name):
        data = self.command('Page.captureScreenshot', {'format': 'png'})['data']
        (OUT / name).write_bytes(base64.b64decode(data))

    def requests(self):
        return [e['params']['request']['url'] for e in self.events if e['method'] == 'Network.requestWillBeSent']

    def close(self):
        try:
            self.command('Browser.close')
        finally:
            self.socket.close()
            self.process.wait(timeout=10)
            self.log.close()


def main():
    global OUT
    OUT.mkdir(parents=True, exist_ok=True)
    OUT = Path(tempfile.mkdtemp(prefix='browser-', dir=OUT))
    runtime = Path(tempfile.mkdtemp(prefix='campus-loading-'))
    front_port, port = free_port(), free_port()
    sys.path.insert(0, str(ROOT / 'frontend'))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from pages.rendering import render_document
    html, _ = render_document({'template': 'index.html', 'context': {
        'user': {'is_authenticated': True, 'is_staff': False}, 'nickname': '로딩 테스트', 'ads_enabled': False,
        'view_config': {'canZoom': False, 'guidePending': False},
        'presence_config': {'avatar': 'male-1'},
        'session_policy_config': {'activityIntervalSeconds': 60, 'statusUrl': '/accounts/session/status/',
                                  'activityUrl': '/accounts/session/activity/'}, 'csrf_token': 'test-token',
    }}, ROOT / 'frontend')
    badges = [('수상한 친화력', 'manager-greeting.png'), ('참을성 테스트', 'manager-poking.png'),
              ('뱀을 길들이는 자', 'python-intermediate.png'), ('데이터의 비밀문', 'sql-intermediate.png'),
              ('찰나의 기적', 'timer.png'), ('손가락에 불났다', 'code-typing.png'),
              ('부서지기 직전', 'fragile-pepero-v1.webp'), ('컴퓨터의 속마음', 'cs-intermediate.png'),
              ('꾸왕이의 비밀 레시피', 'secret-recipe.png')]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, kind = 200, 'application/json'
            if self.path == '/':
                value, kind = html.encode(), 'text/html; charset=utf-8'
            elif self.path == '/api/campus/freeze/':
                value = json.dumps({'paused': False, 'revision': 0}).encode()
            elif self.path == '/api/event-story/achievements/':
                owner = 'notebook_owner=1' in self.headers.get('Cookie', '')
                value = json.dumps({'achievements': [{'event_uuid': str(i), 'name': name, 'achieved': owner and i == 0,
                        'completed_at': '2026-11-07T00:00:00Z' if owner and i == 0 else None,
                        'image_url': '/static/campus/assets/achievements/' + file}
                        for i, (name, file) in enumerate(badges)]}).encode()
            elif self.path == '/health/':
                value = b'{}'
            else:
                status, value = 503, b'{"message":"Browser fixture: no game progress writes"}'
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(value)))
            self.end_headers()
            self.wfile.write(value)
        def do_POST(self):
            self.do_GET()
        def log_message(self, *_):
            pass
    front = ThreadingHTTPServer(('127.0.0.1', front_port), Handler)
    threading.Thread(target=front.serve_forever, daemon=True).start()
    config = f'''daemon off;
worker_processes 1;
pid {runtime}/nginx.pid;
error_log {runtime}/error.log;
events {{ worker_connections 2048; }}
http {{
 include /opt/homebrew/etc/nginx/mime.types;
 access_log {runtime}/access.log;
 map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
 server {{ listen 127.0.0.1:{port};
  proxy_http_version 1.1;
  proxy_set_header Host $http_host;
  proxy_set_header Cookie $http_cookie;
  proxy_set_header Origin $http_origin;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection $connection_upgrade;
  location /static/campus/ {{ alias {ROOT}/frontend/dist/; }}
  location /static/accounts/ {{ alias {ROOT}/frontend/accounts/static/accounts/; }}
  location /static/learning/ {{ alias {ROOT}/frontend/learning/static/learning/; }}
  location / {{ proxy_pass http://127.0.0.1:{front_port}; }}
 }}
}}
'''
    (runtime / 'nginx.conf').write_text(config)
    nginx = subprocess.Popen(['/opt/homebrew/bin/nginx', '-p', str(runtime) + '/', '-c', str(runtime / 'nginx.conf')],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = None
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{front_port}/health/', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        browser = Browser()
        browser.command('Fetch.enable', {'patterns': [{'urlPattern': '*/minigames/timer.html*', 'requestStage': 'Request'}]})
        browser.command('Page.navigate', {'url': f'http://127.0.0.1:{port}/'})
        browser.wait('window.CampusLoading && document.getElementById("entryProgress")')
        for _ in range(100):
            paused = next((e['params']['requestId'] for e in browser.events if e['method'] == 'Fetch.requestPaused'), None)
            if paused:
                break
            browser.evaluate('document.readyState')
            time.sleep(.05)
        assert paused, 'No preloaded game request was intercepted'
        blocked = browser.evaluate('({percent:document.getElementById("entryProgress").value,app:!!window.CampusApp,hidden:document.getElementById("campusEntry").hidden})')
        assert int(blocked['percent']) < 100 and not blocked['app'] and not blocked['hidden'], blocked
        assert not [e for e in browser.events if e['method'] == 'Network.webSocketCreated']
        browser.screenshot('loading.png')
        browser.command('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
        assert browser.evaluate('(()=>{const r=document.querySelector("#campusEntry .entry-copy").getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})()')
        browser.screenshot('loading-mobile.png')
        browser.command('Emulation.clearDeviceMetricsOverride')
        browser.command('Fetch.continueRequest', {'requestId': paused})
        browser.command('Fetch.disable')
        browser.wait('window.CampusApp && document.getElementById("campusEntry").hidden')
        assert browser.evaluate('String(document.getElementById("entryProgress").value)+"%"') == '100%'
        assert not [url for url in browser.requests() if '/assets/achievements/' in url or '/assets/roof-slides/' in url]
        game_requests = [url for url in browser.requests() if '/minigames/' in url and '.html' in url or '/event-games/' in url and '.html' in url]
        assert len(game_requests) == 5, game_requests
        browser.screenshot('ready.png')
        for game in ['timer', 'typing', 'term_match']:
            browser.evaluate('''(()=>{window.__loadingGameReady=false;const frame=document.createElement('iframe');
             frame.dataset.sessionId='loading-browser-test';frame.style.cssText='position:fixed;left:-20000px;width:800px;height:600px';
             window.addEventListener('message',function receive(e){if(e.source===frame.contentWindow&&e.origin===location.origin&&e.data?.type==='minigame:ready'){window.__loadingGameReady=true;window.removeEventListener('message',receive);frame.remove();}});
             frame.src=CampusLoading.gameURL('minigames/''' + game + '''.html?v=20261105-typing-replay-v1');document.body.append(frame);})()''')
            browser.wait('window.__loadingGameReady')
        browser.evaluate('''(()=>{document.querySelectorAll('dialog[open]').forEach(d=>d.close());
         const api=CampusMinigames.create({start:async()=>({play_uuid:'loading-browser-test',game_code:'timer',game_name:'테스트',config:{targetMs:11110}}),isBlocked:()=>false,onOpen(){},onClose(){}});
         api.open({id:'browser-fixture'});})()''')
        browser.wait('document.getElementById("minigameStatus").textContent==="게임을 진행해 주세요."')
        browser.evaluate('document.getElementById("minigameFrame").replaceChildren();document.getElementById("minigameDialog").close()')
        assert len([url for url in browser.requests() if '/minigames/' in url and '.html' in url or '/event-games/' in url and '.html' in url]) == 5
        browser.evaluate('document.querySelectorAll("dialog[open]").forEach(d=>d.close());document.getElementById("achievementsOpen").click()')
        browser.wait('document.getElementById("achievementsDialog").open')
        browser.wait('[...document.querySelectorAll("#achievementsList img")].some(img=>img.src.includes("/assets/achievements/"))')
        browser.evaluate('document.getElementById("achievementsDialog").close();document.querySelectorAll("dialog[open]").forEach(d=>d.close());document.getElementById("floorTransitionDialog").showModal();CampusAds.startPortal()')
        browser.wait('document.getElementById("floorTransitionAdSlot").children.length===2')
        private_views = {}
        for account, value in [('owner', '1'), ('new_account', '0')]:
            browser.command('Network.setCookie', {'name': 'notebook_owner', 'value': value, 'domain': '127.0.0.1', 'path': '/'})
            previous = browser.evaluate('performance.timeOrigin')
            browser.command('Page.reload')
            browser.wait(f'performance.timeOrigin!=={previous}&&window.CampusApp&&document.getElementById("campusEntry").hidden')
            browser.evaluate('document.querySelectorAll("dialog[open]").forEach(d=>d.close());document.getElementById("achievementsOpen").click()')
            browser.wait('document.querySelector("#achievementsList img")?.complete&&document.querySelector("#achievementsList img").naturalWidth>0')
            view = browser.evaluate('''(()=>{const rows=[...document.querySelectorAll('#achievementsList li')];return {
                earned:rows.filter(row=>row.dataset.achieved==='true').length,
                firstFilter:getComputedStyle(rows[0].querySelector('img')).filter,
                exposesOtherOwner:!!document.querySelector('.first-discoverer')};})()''')
            assert view['earned'] == (1 if account == 'owner' else 0), view
            assert view['firstFilter'] == ('none' if account == 'owner' else 'brightness(0)'), view
            assert not view['exposesOtherOwner'], view
            private_views[account] = view
            browser.screenshot(account + '-notebook.png')
        result = {'scope': 'real rendered frontend; mock account/APIs; no backend/WS capacity validation',
                  'loading_gate_blocked_before_completion': blocked, 'preloaded_games': len(game_requests),
                  'all_three_blob_games_reported_ready_from_same_origin': True, 'game_files_fetched_again_on_open': False,
                  'timer_started_through_the_real_minigame_controller': True, 'mobile_loading_card_fits_viewport': True,
                  'badges_and_slides_absent_at_entry': True, 'notebook_loads_images_on_open': True,
                  'floor_transition_loads_two_slides_on_open': True,
                  'private_notebook_views': private_views,
                  'runtime_errors': [e['params'].get('exceptionDetails', {}) for e in browser.events if e['method'] == 'Runtime.exceptionThrown']}
        assert not result['runtime_errors'], result['runtime_errors']
        (OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'result_file': str(OUT / 'result.json'), **result}, ensure_ascii=False))
    except BaseException:
        if browser:
            browser.screenshot('failure.png')
            (OUT / 'failure.json').write_text(json.dumps({'url': browser.evaluate('location.href'),
                'body': browser.evaluate('document.body.innerText').__str__()[:3000],
                'errors': [e for e in browser.events if e['method'] == 'Runtime.exceptionThrown']}, ensure_ascii=False, indent=2))
        raise
    finally:
        if browser:
            browser.close()
        if nginx.poll() is None:
            nginx.send_signal(signal.SIGQUIT)
            nginx.wait(timeout=10)
        front.shutdown()
        front.server_close()


if __name__ == '__main__':
    main()
