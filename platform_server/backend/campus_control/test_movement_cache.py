from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from . import service


class MovementAvailabilityCacheTests(SimpleTestCase):
    async def check_state(self, snapshot, until):
        with patch.object(service, '_snapshot', snapshot), patch.object(service, '_until', until), \
                patch.object(service.time, 'monotonic', return_value=10.0), \
                patch.object(service, '_ensure_refresh') as refresh:
            state = await service.movement_state()
            return state, refresh.call_count

    async def test_fresh_snapshot_needs_no_database_refresh(self):
        state, calls = await self.check_state(({'paused':False,'revision':3},9.5), 10.5)
        self.assertFalse(state['paused']);self.assertEqual(calls,0)

    async def test_refresh_in_progress_keeps_recent_state_without_waiting(self):
        state, calls = await self.check_state(({'paused':False,'revision':3},8.0), 9.0)
        self.assertFalse(state['paused']);self.assertEqual(calls,1)

    async def test_three_second_old_snapshot_blocks_even_staff(self):
        state, calls = await self.check_state(({'paused':False,'revision':3},7.0), 8.0)
        user=SimpleNamespace(is_authenticated=True,is_active=True,is_staff=True)
        self.assertTrue(service.for_user(state,user)['blocked'])
        self.assertTrue(state['unavailable']);self.assertEqual(calls,1)

    async def test_first_read_blocks_until_a_snapshot_is_available(self):
        state, calls = await self.check_state(None,0)
        self.assertTrue(state['unavailable']);self.assertEqual(calls,1)

    def test_concurrent_refresh_requests_share_one_pending_job(self):
        pending=Future()
        with patch.object(service,'_refresh_future',None), \
                patch.object(service._refresh_executor,'submit',return_value=pending) as submit:
            self.assertIs(service._ensure_refresh(),pending)
            self.assertIs(service._ensure_refresh(),pending)
            submit.assert_called_once()
