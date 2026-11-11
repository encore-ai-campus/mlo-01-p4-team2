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

    def test_invalid_ports_and_bind_are_rejected_before_start(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for args in (['--frontend-port', '8000'], ['--backend-base-port', '65534'], ['--bind', 'bad;value']):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    launcher.parse_args(['service', *args])

    def test_required_environment_and_production_defaults(self):
        values = {name: 'test-value' for name in (
            'DJANGO_SECRET_KEY', 'FRONTEND_SECRET_KEY', 'DJANGO_ALLOWED_HOSTS',
            'FRONTEND_ALLOWED_HOSTS', 'DJANGO_CSRF_TRUSTED_ORIGINS', 'MYSQL_HOST',
            'MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD',
            'CAMPUS_MOVEMENT_REDIS_URL', 'CAMPUS_SERVICE_REDIS_URL')}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env_file = Path(directory) / '.env'
            env_file.write_text('\n'.join(f'{key}={value}' for key, value in values.items()))
            env = launcher.load_environment(env_file)
            self.assertEqual(env['DJANGO_DEBUG'], '0')
            self.assertEqual(env['FRONTEND_DEBUG'], '0')
            self.assertEqual(env['DATABASE_ENGINE'], 'mysql')
            env_file.write_text('DJANGO_SECRET_KEY=test-only\n')
            with self.assertRaisesRegex(ValueError, 'Missing environment variables'):
                launcher.load_environment(env_file)

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
