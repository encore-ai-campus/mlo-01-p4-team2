#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
PYTHON=${PYTHON:-python3}
TEST_DATA_DIR=$(mktemp -d "${TMPDIR:-/tmp}/playdata-onprem-test.XXXXXX")
trap 'rm -rf "$TEST_DATA_DIR"' 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
export CAMPUS_DATA_DIR="$TEST_DATA_DIR"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
export DATABASE_ENGINE=sqlite MYSQL_POOL_SIZE=0
export CAMPUS_REDIS_URL= CAMPUS_MOVEMENT_REDIS_URL= CAMPUS_SERVICE_REDIS_URL=
export KAFKA_BOOTSTRAP_SERVERS= CAMPUS_SESSION_CLEANUP_ENABLED=0
export DJANGO_SECRET_KEY=onprem-test-only-not-for-deployment
export FRONTEND_SECRET_KEY=onprem-frontend-test-only-not-for-deployment

case "${1:-python}" in
    python)
        cd "$ROOT/frontend"
        "$PYTHON" manage.py test --settings=config.settings --noinput
        "$PYTHON" manage.py collectstatic --settings=config.settings --noinput
        DJANGO_SETTINGS_MODULE=config.settings "$PYTHON" "$ROOT/deploy/onprem/check_static.py"
        cd "$ROOT"
        "$PYTHON" -m unittest discover -s deploy/onprem -p test_load.py
        "$PYTHON" -m unittest discover -s deploy/onprem -p test_run.py
        cd "$ROOT/backend"
        "$PYTHON" manage.py test --settings=deploy.onprem.test_settings --noinput \
            accounts.test_single_login accounts.test_sessions accounts.test_session_queries \
            campus.test_redis_roles campus.test_authoritative_socket \
            campus.test_movement_outbox campus.test_chat campus.test_single_login \
            campus.test_session_policy campus.test_cluster config.tests
        ;;
    node)
        cd "$ROOT/frontend"
        node --test tests/*.test.cjs
        ;;
    *)
        echo "Usage: $0 [python|node]" >&2
        exit 2
        ;;
esac
