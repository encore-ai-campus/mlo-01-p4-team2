"""Run one PC's native Ubuntu/WSL2 frontend, Nginx, and backend processes."""
import argparse
import ipaddress
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = Path(__file__).resolve().parent


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("movement", "service"))
    parser.add_argument("--env-file", type=Path, default=DEPLOY / ".env")
    parser.add_argument("--frontend-port", type=int, default=8004)
    parser.add_argument("--frontend-backlog", type=int, default=128)
    parser.add_argument("--nginx-port", type=int, default=8000)
    parser.add_argument("--backend-base-port", type=int)
    parser.add_argument("--bind", default="127.0.0.1")
    options = parser.parse_args(argv)
    if options.frontend_backlog < 1:
        parser.error("--frontend-backlog must be positive")
    if options.backend_base_port is None:
        options.backend_base_port = 9101 if options.role == "movement" else 9201
    options.backend_ports = list(range(options.backend_base_port, options.backend_base_port + 4))
    ports = [options.frontend_port, options.nginx_port, *options.backend_ports]
    if len(set(ports)) != len(ports) or any(not 1 <= port <= 65535 for port in ports):
        parser.error("Choose six different ports between 1 and 65535")
    try:
        ipaddress.IPv4Address(options.bind)
    except ValueError:
        parser.error("--bind must be an IPv4 address")
    return options


def load_environment(env_file):
    from dotenv import dotenv_values

    if not env_file.is_file():
        raise ValueError(f"Environment file not found: {env_file}")
    env = {**dotenv_values(env_file), **os.environ}
    required = (
        "DJANGO_SECRET_KEY", "FRONTEND_SECRET_KEY", "DJANGO_ALLOWED_HOSTS",
        "FRONTEND_ALLOWED_HOSTS", "DJANGO_CSRF_TRUSTED_ORIGINS", "POSTGRES_HOST",
        "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
        "CAMPUS_MOVEMENT_REDIS_URL", "CAMPUS_SERVICE_REDIS_URL",
    )
    missing = [key for key in required if not (env.get(key) or "").strip()]
    if missing:
        raise ValueError("Missing environment variables: " + ", ".join(missing))
    env = {key: value for key, value in env.items() if value is not None}
    env.update(DJANGO_DEBUG="0", FRONTEND_DEBUG="0", DATABASE_ENGINE="postgresql",
               DJANGO_SETTINGS_MODULE="config.settings", PYTHONUNBUFFERED="1")
    return env


def nginx_path(path):
    value = str(path.resolve())
    if any(character in value for character in "\n\r$"):
        raise ValueError("Nginx paths must not contain newlines or dollar signs")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_nginx_config(options):
    runtime = DEPLOY / "var" / f"{options.role}-{options.nginx_port}"
    runtime.mkdir(parents=True, exist_ok=True)
    values = {
        "RUNTIME": nginx_path(runtime), "BIND": options.bind,
        "NGINX_PORT": str(options.nginx_port),
        "STATIC": nginx_path(ROOT / "frontend" / "staticfiles"),
        **{f"BACKEND_{index}": str(port) for index, port in enumerate(options.backend_ports, 1)},
    }
    config = (DEPLOY / f"nginx-{options.role}.conf").read_text()
    for key, value in values.items():
        config = config.replace(f"@{key}@", value)
    destination = runtime / "nginx.conf"
    destination.write_text(config)
    return runtime, destination


def check_ports(options):
    addresses = [("127.0.0.1", port) for port in options.backend_ports]
    addresses += [(options.bind, options.frontend_port), (options.bind, options.nginx_port)]
    for address in addresses:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(address)


def check_children(processes):
    for name, process in processes:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited with status {process.returncode}")


def wait_ready(addresses, processes):
    deadline = time.monotonic() + 30
    pending = set(addresses)
    while pending:
        check_children(processes)
        for address in pending.copy():
            try:
                with socket.create_connection(address, timeout=.2):
                    pending.remove(address)
            except OSError:
                pass
        if pending and time.monotonic() >= deadline:
            raise RuntimeError("Startup timed out waiting for listening ports")
        time.sleep(.1)


def stop_children(processes):
    for _, process in reversed(processes):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    for _, process in reversed(processes):
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def interrupt(*_):
    raise KeyboardInterrupt


def main(argv=None):
    options = parse_args(argv)
    processes = []
    previous_handlers = {}
    try:
        if os.name != "posix":
            raise ValueError("Run this launcher inside Ubuntu/WSL2")
        env = load_environment(options.env_file.resolve())
        nginx = shutil.which("nginx")
        if not nginx:
            raise ValueError("Install nginx and add it to PATH")
        check_ports(options)
        runtime, config = write_nginx_config(options)
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupt)
        nginx_command = [nginx, "-e", "stderr", "-p", str(runtime) + "/", "-c", str(config)]
        subprocess.run([*nginx_command, "-t"], check=True)
        env["BACKEND_URL"] = env["MOVEMENT_BACKEND_URL"] = f"http://127.0.0.1:{options.nginx_port}"
        if options.bind not in ("127.0.0.1", "0.0.0.0"):
            env["BACKEND_URL"] = env["MOVEMENT_BACKEND_URL"] = f"http://{options.bind}:{options.nginx_port}"
        if options.role == "service":
            subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput", "--clear"],
                           cwd=ROOT / "frontend", env=env, check=True)

        def start(name, command, directory):
            process = subprocess.Popen(command, cwd=directory, env=env, start_new_session=True)
            processes.append((name, process))

        for port in options.backend_ports:
            start(f"backend:{port}", [sys.executable, "-m", "daphne", "-b", "127.0.0.1",
                  "-p", str(port), f"config.{options.role}_asgi:application"], ROOT / "backend")
        wait_ready([("127.0.0.1", port) for port in options.backend_ports], processes)
        start("nginx", [*nginx_command, "-g", "daemon off;"], ROOT)
        origin_host = "127.0.0.1" if options.bind == "0.0.0.0" else options.bind
        wait_ready([(origin_host, options.nginx_port)], processes)
        start("frontend", [sys.executable, "-m", "daphne", "-e",
              f"tcp:port={options.frontend_port}:interface={options.bind}:backlog={options.frontend_backlog}",
              f"config.{options.role}_asgi:application"], ROOT / "frontend")
        wait_ready([(origin_host, options.frontend_port)], processes)
        if options.role == "service":
            start("jobs", [sys.executable, "manage.py", "campus_jobs"], ROOT / "backend")
        print(f"{options.role} frontend: http://{origin_host}:{options.frontend_port}", flush=True)
        print("Ctrl+C stops this PC's processes.", flush=True)
        while True:
            check_children(processes)
            time.sleep(.2)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Startup/runtime failed: {error}", file=sys.stderr)
        return 1
    finally:
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            stop_children(processes)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
