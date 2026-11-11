from tests.factories import create_profile

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from accounts.models import Position


class ExactJSONStorageTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='precision-check')
        self.profile = create_profile(user=user, nickname='precision')
        self.saved_position = Position.objects.get(profile=self.profile)
        self.position = {'floor': 'campus', 'x': 1909.0846645249294,
                         'y': 920.9031138666909, 'dir': 'left'}

    def test_last_position_preserves_coordinates_and_json_lookups(self):
        self.saved_position.last_position = self.position
        self.saved_position.save(update_fields=['last_position'])
        self.saved_position.refresh_from_db()
        self.assertEqual(self.saved_position.last_position, self.position)
        self.assertTrue(Position.objects.filter(pk=self.profile.pk,
                        last_position__floor='campus').exists())
        self.assertTrue(Position.objects.filter(pk=self.profile.pk,
                        last_position__floor__icontains='camp').exists())


    def test_database_still_rejects_invalid_json(self):
        table = connection.ops.quote_name(Position._meta.db_table)
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('UPDATE ' + table + ' SET last_position=%s WHERE user_uuid=%s',
                               ['{invalid json}', self.profile.pk])
