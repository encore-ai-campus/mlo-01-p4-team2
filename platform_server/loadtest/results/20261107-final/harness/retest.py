import json,os,sys
from pathlib import Path
root=Path('/private/tmp/platform-final-integration-20261107')
os.environ.update(json.loads((root/'environment.json').read_text()))
os.environ.update(DJANGO_SETTINGS_MODULE='config.postgres_test_settings',CAMPUS_REDIS_URL='',CAMPUS_MOVEMENT_REDIS_URL='',CAMPUS_SERVICE_REDIS_URL='',CAMPUS_DATA_DIR=str(root/'retest-data'))
Path(os.environ['CAMPUS_DATA_DIR']).mkdir(exist_ok=True)
sys.path.insert(0,str(root/'source/backend'));os.chdir(root/'source/backend')
import django
django.setup()
from django.conf import settings
from django.test.utils import get_runner
labels=['campus.test_load_reduction.IdleTrafficTests.test_a_slow_position_save_does_not_stall_walking','campus.test_reconnect_transitions.ReconnectSocketTests.test_live_old_socket_is_replaced_and_late_disconnect_keeps_new_player']
raise SystemExit(bool(get_runner(settings)(verbosity=2,interactive=False).run_tests(labels)))
