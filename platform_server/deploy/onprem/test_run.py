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
