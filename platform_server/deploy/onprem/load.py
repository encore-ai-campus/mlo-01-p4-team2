"""Small load runner for disposable sessions; this is not a measured capacity claim."""
import argparse
import asyncio
from collections import Counter
import http.client
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def validate(args):
    for value, schemes in ((args.service_http_base, {'http', 'https'}),
                           (args.movement_ws_url, {'ws', 'wss'}),
                           (args.chat_ws_url, {'ws', 'wss'}), (args.origin, {'http', 'https'})):
        url = urlsplit(value)
        if (url.scheme not in schemes or not url.hostname or url.username or url.password
                or url.fragment or any(character.isspace() or ord(character) < 32 for character in value)):
            raise ValueError('Invalid endpoint URL')
        if url.port is not None and url.port < 1:
            raise ValueError('Invalid endpoint port')
    for value in (args.service_http_base, args.origin):
        url = urlsplit(value)
        if url.path not in ('', '/') or url.query:
            raise ValueError('HTTP base and origin must contain only scheme and host')
    if (args.users < 1 or args.duration <= 0 or args.ramp < 0 or args.chat_interval < 0
            or not all(math.isfinite(value) for value in (args.duration, args.ramp, args.chat_interval))):
        raise ValueError('Invalid load size or duration')
    sessions = json.loads(Path(args.sessions_json).read_text())
    if not isinstance(sessions, list) or len(sessions) < args.users:
        raise ValueError('Not enough disposable sessions')
    sessions = sessions[:args.users]
    if any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9]{1,128}', key) for key in sessions):
        raise ValueError('Invalid session key')
    if len(set(sessions)) != len(sessions):
        raise ValueError('Each user must have a different session')
    return ['campus_sessionid=' + key for key in sessions]


def http_status(args, cookie):
    target, origin = urlsplit(args.service_http_base), urlsplit(args.origin)
    factory = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
    connection = factory(target.hostname, target.port, timeout=5)
    try:
        connection.request('GET', '/accounts/session/status/', headers={
            'Host': origin.netloc, 'Cookie': cookie, 'Origin': args.origin.rstrip('/'),
            'X-Forwarded-Proto': origin.scheme})
        response = connection.getresponse()
        body = response.read()
        if response.status == 200:
            state = json.loads(body)
            if not isinstance(state, dict) or state.get('authenticated') is not True:
                return 401
        return response.status
    finally:
        connection.close()


def websocket(args, target_url, cookie):
    target, origin = urlsplit(target_url), urlsplit(args.origin)
    # Preserve the public Host while connecting directly to a private origin.
    public_url = urlunsplit((target.scheme, origin.netloc, target.path, target.query, ''))
    return connect(public_url, host=target.hostname, port=target.port or (443 if target.scheme == 'wss' else 80),
                   origin=args.origin.rstrip('/'), additional_headers={
                       'Cookie': cookie, 'X-Forwarded-Proto': origin.scheme},
                   open_timeout=10, close_timeout=2, ping_interval=None, proxy=None)


async def expected(socket, kind):
    async with asyncio.timeout(10):
        while True:
            message = json.loads(await socket.recv())
            if message.get('type') == kind:
                return message


async def user(args, cookie, index, deadline, stats):
    tasks, closed = [], set()
    try:
        await asyncio.sleep(index * args.ramp / args.users)
        async with websocket(args, args.movement_ws_url, cookie) as movement:
            welcome = await expected(movement, 'welcome')
            token = [welcome.get('chatToken')]
            if not isinstance(token[0], str) or not token[0]:
                raise ValueError('Missing actor token')
            async with websocket(args, args.chat_ws_url, cookie) as chat:
                await expected(chat, 'chat_ready')
                stats['ready'] += 1

                async def read(socket):
                    try:
                        while True:
                            message = json.loads(await socket.recv())
                            if isinstance(message.get('chatToken'), str):
                                token[0] = message['chatToken']
                            if message.get('type') in ('chat_error', 'control_error', 'session_expired'):
                                stats['application_errors'] += 1
                    except ConnectionClosed:
                        closed.add(id(socket))
                        raise

                async def send():
                    next_ping = next_chat = 0
                    while True:
                        now = time.monotonic()
                        keys = ['w'] if int(now) % 4 < 2 else ['s']
                        await movement.send(json.dumps({'type': 'input', 'keys': keys}))
                        if now >= next_ping:
                            for socket in (movement, chat):
                                await socket.send('{"type":"ping"}')
                            next_ping = now + 5
                        if args.chat_interval and now >= next_chat:
                            await chat.send(json.dumps({'type': 'chat', 'text': 'load test', 'actorToken': token[0]}))
                            next_chat = now + args.chat_interval
                        await asyncio.sleep(.1)

                async def poll():
                    while True:
                        started = time.monotonic()
                        try:
                            status = await asyncio.to_thread(http_status, args, cookie)
                            stats['http_errors'] += int(status != 200)
                        except Exception as error:
                            stats['http_errors'] += 1
                            stats['errors'][type(error).__name__] += 1
                        stats['http_ms'].append((time.monotonic() - started) * 1000)
                        await asyncio.sleep(15)

                tasks = [asyncio.create_task(task()) for task in (lambda: read(movement), lambda: read(chat), send, poll)]
                try:
                    async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                        await asyncio.gather(*tasks)
                except TimeoutError:
                    pass
    except Exception as error:
        stats['failed'] += 1
        if isinstance(error, ConnectionClosed) and not closed:
            closed.add('before-reader')
        stats['errors'][type(error).__name__] += 1
    finally:
        stats['closed_connections'] += len(closed)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run(args, cookies):
    stats = {'ready': 0, 'failed': 0, 'closed_connections': 0, 'application_errors': 0,
             'http_errors': 0, 'http_ms': [], 'errors': Counter()}
    deadline = time.monotonic() + args.ramp + args.duration
    await asyncio.gather(*(user(args, cookie, index, deadline, stats) for index, cookie in enumerate(cookies)))
    latencies = sorted(stats.pop('http_ms'))
    stats['http_requests'] = len(latencies)
    stats['http_error_rate'] = stats['http_errors'] / len(latencies) if latencies else 0
    for percentile in (50, 95, 99):
        stats[f'http_p{percentile}_ms'] = latencies[math.ceil(len(latencies) * percentile / 100) - 1] if latencies else None
    stats.update(users=args.users, duration_seconds=args.duration, ramp_seconds=args.ramp,
                 success=stats['ready'] == args.users and not any(stats[key] for key in ('failed', 'http_errors', 'application_errors')),
                 scope='Generated load only; this tool is not evidence of measured production capacity')
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('service-http-base', 'movement-ws-url', 'chat-ws-url', 'origin', 'sessions-json'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--users', type=int, default=150)
    parser.add_argument('--duration', type=float, default=60)
    parser.add_argument('--ramp', type=float, default=30)
    parser.add_argument('--chat-interval', type=float, default=0)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args, validate(args)))
    except (ValueError, OSError):
        print(json.dumps({'success': False, 'error': 'Invalid configuration or session file'}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
