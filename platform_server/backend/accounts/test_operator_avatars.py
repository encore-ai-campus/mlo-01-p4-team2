from types import SimpleNamespace

from django.test import SimpleTestCase

from accounts.avatars import AVATAR_CHOICES, AVATARS, OPERATOR_AVATAR_IDS, avatar_options
from accounts.cohorts import allowed_avatars, course_for_cohort
from accounts.models import get_avatar


class OperatorAvatarTests(SimpleTestCase):
    def test_operator_variants_are_not_public_signup_choices(self):
        public_ids = {avatar["id"] for avatar in AVATARS}
        offered_ids = {option["id"] for option in avatar_options()}
        self.assertEqual(set(dict(AVATAR_CHOICES)), public_ids)
        self.assertTrue(OPERATOR_AVATAR_IDS.isdisjoint(public_ids))
        self.assertTrue(OPERATOR_AVATAR_IDS.isdisjoint(offered_ids))
        self.assertEqual(len(OPERATOR_AVATAR_IDS), 5)

    def test_operator_cohort_is_labeled_without_becoming_a_signup_cohort(self):
        self.assertEqual(course_for_cohort("ops-team"), "운영팀")
        self.assertEqual(allowed_avatars("ops-team"), ())

    def test_only_staff_can_render_private_operator_variants(self):
        for avatar in OPERATOR_AVATAR_IDS:
            profile = SimpleNamespace(avatar=avatar)
            regular = SimpleNamespace(campus_profile=profile, is_staff=False, is_superuser=False)
            staff = SimpleNamespace(campus_profile=profile, is_staff=True, is_superuser=False)
            superuser = SimpleNamespace(campus_profile=profile, is_staff=False, is_superuser=True)
            with self.subTest(avatar=avatar):
                self.assertEqual(get_avatar(regular), "male-1")
                self.assertEqual(get_avatar(staff), avatar)
                self.assertEqual(get_avatar(superuser), avatar)
