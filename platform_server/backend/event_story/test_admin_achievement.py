from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from . import achievement_services
from .achievement_services import KKUWANG_HIT_ID, complete_achievement


class AdminAchievementBlockTests(SimpleTestCase):
    def test_staff_and_superusers_are_rejected_before_achievement_lookup(self):
        for user in (
            SimpleNamespace(is_staff=True, is_superuser=False),
            SimpleNamespace(is_staff=True, is_superuser=True),
            SimpleNamespace(is_staff=False, is_superuser=True),
        ):
            with self.subTest(is_staff=user.is_staff, is_superuser=user.is_superuser):
                with patch.object(achievement_services, "get_user_model") as get_model:
                    self.assertFalse(complete_achievement.__wrapped__(user, KKUWANG_HIT_ID, timezone.now()))
                    get_model.assert_not_called()

    def test_role_change_after_login_is_checked_from_locked_database_account(self):
        stale_user = SimpleNamespace(pk=7, is_staff=False, is_superuser=False)
        locked_account = SimpleNamespace(is_active=True, is_staff=True, is_superuser=False)
        manager = Mock()
        manager.select_for_update.return_value.get.return_value = locked_account
        user_model = SimpleNamespace(objects=manager)

        with patch.object(achievement_services, "get_user_model", return_value=user_model), \
             patch("campus_control.event_access.snapshot", return_value={"open": True}):
            self.assertFalse(complete_achievement.__wrapped__(stale_user, KKUWANG_HIT_ID, timezone.now()))

        manager.select_for_update.assert_called_once_with()
        manager.select_for_update.return_value.get.assert_called_once_with(pk=stale_user.pk)
