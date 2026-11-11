"""Read-only export of an old database, including excluded records for archival."""
import json
import os
from datetime import date, datetime, time
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction


class DatabaseEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        return super().default(value)


class Command(BaseCommand):
    help = 'Export platform tables without changing the source database. The file contains private account data.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True, type=Path)
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        connection = connections[options['database']]
        tables = {}
        try:
            if connection.vendor == 'mysql':
                with connection.cursor() as cursor:
                    cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            # Stop writers before exporting. This is a consistent transaction, not an online cutover tool.
            with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
                if connection.vendor == 'postgresql':
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                for table in connection.introspection.table_names(cursor):
                    if table == 'django_migrations':
                        continue
                    cursor.execute('SELECT * FROM ' + connection.ops.quote_name(table))
                    columns = [column[0] for column in cursor.description]
                    tables[table] = [dict(zip(columns, row)) for row in cursor.fetchall()]
            payload = {'format': 'platform-database-v1', 'source_vendor': connection.vendor, 'tables': tables}
            fd = os.open(options['output'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream, cls=DatabaseEncoder, ensure_ascii=False)
        except (OSError, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({'tables': len(tables), 'rows': sum(map(len, tables.values())), 'output': str(options['output'])}))
