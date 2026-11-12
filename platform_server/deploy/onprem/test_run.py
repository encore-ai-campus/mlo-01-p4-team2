"""Launcher checks without binding ports or launching deployment services."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('onprem_run', Path(__file__).with_name('run.py'))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_frontend_backlog_default_override_and_invalid_values(self):
        for role in ('movement', 'service'):
            with self.subTest(role=role):
                self.assertEqual(launcher.parse_args([role]).frontend_backlog, 128)
        self.assertEqual(launcher.parse_args(['service', '--frontend-backlog', '256']).frontend_backlog, 256)
        with contextlib.redirect_stderr(io.StringIO()):
            for value in ('0', '-1'):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    launcher.parse_args(['movement', '--frontend-backlog', value])

    def test_role_templates_and_non_default_ports_preserve_boundaries(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, 'DEPLOY', Path(directory)):
            for role in ('movement', 'service'):
                template = Path(__file__).with_name(f'nginx-{role}.conf').read_text()
                (Path(directory) / f'nginx-{role}.conf').write_text(template)
                options = launcher.parse_args([role, '--frontend-port', '28004', '--nginx-port', '28000'])
                runtime, path = launcher.write_nginx_config(options)
                config = path.read_text()
                self.assertNotIn('@', config)
                self.assertIn('listen 127.0.0.1:28000;', config)
                self.assertIn('proxy_set_header Host $http_host;', config)
                self.assertIn('proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;', config)
                self.assertNotIn('proxy_pass http://127.0.0.1:28004', config)
                self.assertTrue(runtime.is_dir())
                if role == 'movement':
                    self.assertIn('server 127.0.0.1:9101', config)
                    self.assertIn('location / { return 404; }', config)
                else:
                    self.assertIn('server 127.0.0.1:9201', config)
                    self.assertIn('location /static/', config)

    def test_entry_routes_bypass_python_for_both_websockets(self):
        config = Path(__file__).with_name('nginx-entry.conf.example').read_text()
        for path, destination in (
            ('/ws/movement/', '@MOVEMENT_HOST@:@MOVEMENT_NGINX_PORT@'),
            ('/ws/chat/', 'service_ingress'),
            ('/static/', 'service_ingress'),
            ('/', 'service_frontend'),
        ):
            self.assertIn('location ' + ('= ' if path.startswith('/ws/') else '') +
                          path + ' { proxy_pass http://' + destination + '; }', config)
        for upstream, address in (('service_ingress', '@SERVICE_HOST@:@SERVICE_NGINX_PORT@'),
                                  ('service_frontend', '@SERVICE_HOST@:@SERVICE_FRONTEND_PORT@')):
            self.assertIn('upstream ' + upstream + ' {\n        server ' + address + ';', config)
        for header in ('Cookie $http_cookie', 'Origin $http_origin', 'Host $http_host',
                       'X-Forwarded-Proto $scheme'):
            self.assertIn('proxy_set_header ' + header + ';', config)
        self.assertIn('location = /ws/campus/ { return 404; }', config)
        service = Path(__file__).with_name('nginx-service.conf').read_text()
        self.assertIn('upstream service_chat {\n        hash $cookie_campus_sessionid consistent;', service)

    def test_invalid_ports_and_bind_are_rejected_before_start(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for args in (['--frontend-port', '8000'], ['--backend-base-port', '65534'], ['--bind', 'bad;value']):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    launcher.parse_args(['service', *args])

    def test_required_environment_and_production_defaults(self):
        values = {name: 'test-value' for name in (
            'DJANGO_SECRET_KEY', 'FRONTEND_SECRET_KEY', 'DJANGO_ALLOWED_HOSTS',
            'FRONTEND_ALLOWED_HOSTS', 'DJANGO_CSRF_TRUSTED_ORIGINS', 'POSTGRES_HOST',
            'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD',
            'CAMPUS_MOVEMENT_REDIS_URL', 'CAMPUS_SERVICE_REDIS_URL')}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env_file = Path(directory) / '.env'
            env_file.write_text('\n'.join(f'{key}={value}' for key, value in values.items()))
            env = launcher.load_environment(env_file)
            self.assertEqual(env['DJANGO_DEBUG'], '0')
            self.assertEqual(env['FRONTEND_DEBUG'], '0')
            self.assertEqual(env['DATABASE_ENGINE'], 'postgresql')
            env_file.write_text('DJANGO_SECRET_KEY=test-only\n')
            with self.assertRaisesRegex(ValueError, 'Missing environment variables'):
                launcher.load_environment(env_file)

    def test_password_file_replaces_the_password_only_when_it_fits_the_connection(self):
        secret = 'not-a-real-secret-7f3a'
        values = {name: 'test-value' for name in (
            'DJANGO_SECRET_KEY', 'FRONTEND_SECRET_KEY', 'DJANGO_ALLOWED_HOSTS',
            'FRONTEND_ALLOWED_HOSTS', 'DJANGO_CSRF_TRUSTED_ORIGINS',
            'CAMPUS_MOVEMENT_REDIS_URL', 'CAMPUS_SERVICE_REDIS_URL')}
        values.update(POSTGRES_HOST='127.0.0.1', POSTGRES_PORT='5000', POSTGRES_DB='metaverse', POSTGRES_USER='metaverse_app')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env_file, password_file = Path(directory) / '.env', Path(directory) / 'app.pgpass'

            def load(lines, mode=0o600, **extra):
                if password_file.exists():
                    password_file.chmod(0o600)  # writable again after a read-only case
                password_file.write_text(''.join(line + '\n' for line in lines))
                password_file.chmod(mode)
                env_file.write_text('\n'.join(f'{key}={value}' for key, value in
                                              {**values, 'PGPASSFILE': str(password_file), **extra}.items()))
                return launcher.load_environment(env_file)

            fitting = f'127.0.0.1:5000:metaverse:metaverse_app:{secret}'
            env = load(['# comment', f'127.0.0.1:5000:other:metaverse_app:{secret}', fitting])
            self.assertEqual(env['PGPASSFILE'], str(password_file.resolve()))
            self.assertFalse(env.get('POSTGRES_PASSWORD'))  # Django then sends none and libpq reads the file
            refused = (
                ([fitting], 0o640, {}, 'mode 0600, not 0640'),
                ([fitting], 0o400, {}, 'mode 0600, not 0400'),
                (['127.0.0.1:5432:metaverse:metaverse_app:' + secret], 0o600, {}, 'no line for'),
                (['127.0.0.1:5000:metaverse:metaverse_app:'], 0o600, {}, 'no line for'),
                (['# ' + fitting], 0o600, {}, 'no line for'),
                ([fitting], 0o600, {'POSTGRES_USER': 'other'}, 'no line for'),
            )
            for lines, mode, extra, message in refused:
                with self.subTest(message=message, mode=oct(mode), extra=extra):
                    with self.assertRaisesRegex(ValueError, message) as caught:
                        load(lines, mode, **extra)
                    self.assertNotIn(secret, str(caught.exception))
            with patch.object(launcher.os, 'geteuid', return_value=os.geteuid() + 1), \
                    self.assertRaisesRegex(ValueError, 'owned by the user running the launcher'):
                load([fitting])
            with self.assertRaisesRegex(ValueError, 'cannot be read by this user'):
                load([fitting], PGPASSFILE=str(Path(directory) / 'missing.pgpass'))
            with self.assertRaisesRegex(ValueError, r'POSTGRES_PASSWORD \(or PGPASSFILE\)'):
                load([fitting], PGPASSFILE='')
            # A password in the environment wins (libpq ignores the file then), so the file is not checked.
            self.assertEqual(load([], 0o644, POSTGRES_PASSWORD='test-value')['POSTGRES_PASSWORD'], 'test-value')

    @unittest.skipUnless(hasattr(os, "killpg") and hasattr(signal, "SIGKILL"), "POSIX process groups require Ubuntu/WSL")
    def test_child_failure_and_shutdown_escalation(self):
        exited = Mock(returncode=2)
        exited.poll.return_value = 2
        with self.assertRaisesRegex(RuntimeError, 'exited with status 2'):
            launcher.check_children([('backend', exited)])
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired('child', 10), 0]
        with patch.object(launcher.os, 'killpg') as kill:
            launcher.stop_children([('backend', process)])
        self.assertEqual(kill.call_args_list[0].args, (12345, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (12345, signal.SIGKILL))


if __name__ == '__main__':
    unittest.main()
