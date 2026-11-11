"""JSON storage that preserves coordinate number representations."""
import json
from functools import partial
from django.db.models.fields.json import KeyTransform, KeyTransformFactory
from django.db import models


class LosslessKeyTransform(KeyTransform):
    def as_postgresql(self, compiler, connection):
        sql, params = super().as_postgresql(compiler, connection)
        return f"({sql})::jsonb", params


class LosslessJSONField(models.JSONField):
    def get_transform(self, name):
        transform = super().get_transform(name)
        return partial(LosslessKeyTransform, name) if isinstance(transform, KeyTransformFactory) else transform

    def db_type(self, connection):
        if connection.vendor == "postgresql":
            return "json"
        if connection.vendor == "mysql":
            return "longtext"
        return super().db_type(connection)

    def db_check(self, connection):
        if connection.vendor == "mysql":
            return "JSON_VALID(%s)" % connection.ops.quote_name(self.column)
        return super().db_check(connection)

    def get_db_prep_value(self, value, connection, prepared=False):
        if connection.vendor == "postgresql" and not hasattr(value, "as_sql"):
            from psycopg.types.json import Json, Jsonb
            adapter = Jsonb if prepared else Json
            return adapter(value, dumps=lambda obj: json.dumps(obj, cls=self.encoder))
        return super().get_db_prep_value(value, connection, prepared)

    def from_db_value(self, value, expression, connection):
        if value is not None and not isinstance(value, str):
            return value
        return super().from_db_value(value, expression, connection)
