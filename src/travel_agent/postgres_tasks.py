"""PostgreSQL task store for stateless API and worker invocations."""

from contextlib import contextmanager
from time import time

import psycopg

from .task_store import SCHEMA, TaskStore


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def rows(cursor):
    names = [col.name for col in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values, strict=True))


class PostgresSession:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, args=()):
        if query == "BEGIN IMMEDIATE":
            # Serialize admission/claims across invocations just as SQLite's writer lock does.
            return self.connection.execute("SELECT pg_advisory_xact_lock(890214321)")
        return self.connection.execute(query.replace("?", "%s"), args)


class PostgresTaskStore(TaskStore):
    def __init__(self, dsn, *, clock=time):
        self.dsn, self.clock = dsn, clock
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            schema = SCHEMA.replace(" REAL", " DOUBLE PRECISION").replace(" BLOB", " BYTEA")
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)

    def _recover(self, db, now):
        # Cleanup is a series of writes without reads. Send them in one network round trip.
        with db.connection.pipeline():
            super()._recover(db, now)

    @contextmanager
    def db(self):
        with psycopg.connect(self.dsn, connect_timeout=12, row_factory=rows) as connection:
            connection.execute("SET LOCAL lock_timeout TO '15s'")
            yield PostgresSession(connection)
