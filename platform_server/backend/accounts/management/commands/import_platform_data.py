"""Import a stopped legacy source into a fresh PostgreSQL target."""
import json
import uuid
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, models, transaction
from django.utils import timezone


class Command(BaseCommand):
    help = 'Import an export into a fresh PostgreSQL database. Default: validate and roll back. Source dump retains excluded records.'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, type=Path)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('Target must be PostgreSQL.')
        try:
            payload = json.loads(options['file'].read_text())
            if payload['format'] != 'platform-database-v1':
                raise ValueError('Unsupported export format')
            source = payload['tables']
        except (OSError, ValueError, KeyError) as error:
            raise CommandError(str(error)) from error
        User = get_user_model()
        if User.objects.exists():
            raise CommandError('Target already contains accounts; use a fresh migrated target database.')
        profiles = {str(row['user_id']): row for row in source.get('accounts_profile', [])}
        user_ids = {str(row['id']): uuid.UUID(str(profiles[str(row['id'])]['user_uuid']))
                    if str(row['id']) in profiles and profiles[str(row['id'])].get('user_uuid')
                    else self.uuid_or_new(row['id']) for row in source.get('auth_user', [])}
        post_ids = {str(row['id']): row['post_uuid'] for row in source.get('community_post', [])}
        counts = {}
        excluded = {'django_session', 'accounts_activelogin', 'eventing_eventoutbox', 'community_mealcomment'}
        models_by_table = {m._meta.db_table: m for m in apps.get_models(include_auto_created=True)}
        target_models = [m for table, m in models_by_table.items() if table in source and table not in excluded
                         and table not in {'django_content_type', 'auth_permission'}]
        try:
            with transaction.atomic():
                content_types = {}
                for row in source.get('django_content_type', []):
                    label = 'identity' if (row['app_label'], row['model']) == ('auth', 'user') else row['app_label']
                    obj, _ = ContentType.objects.get_or_create(app_label=label, model=row['model'])
                    content_types[row['id']] = obj.pk
                permission_ids = {}
                for row in source.get('auth_permission', []):
                    obj, _ = Permission.objects.get_or_create(content_type_id=content_types[row['content_type_id']],
                        codename=row['codename'], defaults={'name': row['name']})
                    permission_ids[row['id']] = obj.pk
                done = {'django_content_type', 'auth_permission'} | excluded
                pending = list(target_models)
                while pending:
                    progress = False
                    for Model in pending[:]:
                        dependencies = {f.remote_field.model._meta.db_table for f in Model._meta.local_fields
                                        if f.is_relation and f.remote_field.model != Model}
                        if not (dependencies & set(source)) <= done:
                            continue
                        table = Model._meta.db_table
                        objects, times = [], []
                        for row in source[table]:
                            values = {}
                            for field in Model._meta.local_fields:
                                column = field.column
                                raw = row.get(column, models.NOT_PROVIDED)
                                if table == 'community_comment' and field.name == 'post':
                                    raw = row.get('post_uuid', post_ids.get(str(row.get('post_id'))))
                                if table == 'accounts_profile' and field.name == 'cohort':
                                    raw = row.get('cohort', '')
                                    try:
                                        uuid.UUID(str(raw))
                                    except ValueError:
                                        Group = apps.get_model('eventing', 'ClassGroup')
                                        raw = Group.objects.get(code=raw or 'unassigned').pk
                                if raw is models.NOT_PROVIDED:
                                    continue
                                if Model is User and field.primary_key:
                                    raw = user_ids[str(raw)]
                                elif field.is_relation and field.remote_field.model is User:
                                    raw = user_ids[str(raw)]
                                elif column == 'content_type_id':
                                    raw = content_types[raw]
                                elif column == 'permission_id':
                                    raw = permission_ids[raw]
                                if isinstance(field, models.JSONField) and isinstance(raw, str):
                                    raw = json.loads(raw)
                                elif raw is not None and not field.is_relation:
                                    raw = field.to_python(raw)
                                    if isinstance(field, models.DateTimeField) and timezone.is_naive(raw):
                                        raw = timezone.make_aware(raw, datetime_timezone.utc)
                                values[field.attname] = raw
                            if table == 'direct_messages_conversation':
                                values['first_user_id'], values['second_user_id'] = sorted((values['first_user_id'], values['second_user_id']))
                            obj = Model(**values)
                            auto_times = {f.attname: getattr(obj, f.attname) for f in Model._meta.local_fields
                                          if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False)}
                            objects.append(obj); times.append(auto_times)
                        update = [f.name for f in Model._meta.local_fields if not f.primary_key]
                        if objects:
                            Model.objects.bulk_create(objects, update_conflicts=bool(update),
                                update_fields=update or None, unique_fields=[Model._meta.pk.name] if update else None)
                            timestamp_names = list(times[0])
                            if timestamp_names:
                                for obj, original in zip(objects, times):
                                    for name, value in original.items():
                                        setattr(obj, name, value)
                                Model.objects.bulk_update(objects, timestamp_names)
                        counts[table] = len(objects)
                        pending.remove(Model); done.add(table); progress = True
                    if not progress:
                        raise ValueError('Unresolved table dependencies: ' + ', '.join(m._meta.db_table for m in pending))
                Position = apps.get_model('accounts', 'Position')
                for old_user, row in profiles.items():
                    if row.get('last_position') is not None:
                        position = row['last_position']
                        if isinstance(position, str): position = json.loads(position)
                        Position.objects.update_or_create(profile_id=user_ids[old_user], defaults={
                            'last_position': position, 'last_position_at': self.utc_time(row.get('last_position_at'))})
                # Old browser sessions are deliberately not migrated; retain their history but require a fresh login.
                apps.get_model('eventing', 'PlatformSession').objects.filter(ended_at__isnull=True).update(
                    ended_at=timezone.now(), end_reason='SESSION_EXPIRED')
                with connection.cursor() as cursor:
                    cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                    if options['apply']:
                        for sql in connection.ops.sequence_reset_sql(no_style(), list(models_by_table.values())):
                            cursor.execute(sql)
                        for table in ['community_post', 'community_comment']:
                            cursor.execute(f"SELECT setval('{table}_display_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}")
                if not options['apply']:
                    transaction.set_rollback(True)
        except Exception as error:
            raise CommandError(f'Import rolled back: {error}') from error
        self.stdout.write(json.dumps({'mode': 'applied' if options['apply'] else 'validated-and-rolled-back',
            'copied_rows': counts, 'archived_only': {t: len(source.get(t, [])) for t in sorted(excluded)}}))

    @staticmethod
    def utc_time(value):
        if value is None: return None
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        return timezone.make_aware(value, datetime_timezone.utc) if timezone.is_naive(value) else value

    @staticmethod
    def uuid_or_new(value):
        try: return uuid.UUID(str(value))
        except ValueError: return uuid.uuid4()
