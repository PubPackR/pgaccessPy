"""Generic, reusable Postgres access. No AdManager references, no pandas:
library-clean.

House fixes over the crawler helper: real transactions via transaction()
(commit on success, rollback on exception) -- connections are NOT put into
autocommit at connect time; and fail-loud -- errors propagate, nothing swallowed.
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from .credentials import CredentialManager


def connect(config=None):
    """Open a psycopg2 connection. If config is None, resolve via
    CredentialManager. The 'type' marker is dropped (psycopg2 rejects it).
    Does NOT enable autocommit."""
    if config is None:
        config = CredentialManager().get_db_config()
    # Dynamic **config splat: keys come from the keyfile at runtime, so pyright can't
    # match them against psycopg2.connect's overloads.
    conn_kwargs = {k: v for k, v in config.items() if k != "type"}
    return psycopg2.connect(**conn_kwargs)  # pyright: ignore[reportCallIssue, reportArgumentType]


def close(conn):
    conn.close()


class connection:
    """`with connection() as conn:` opens and always closes. Transaction control
    is separate -- use transaction()."""
    def __init__(self, config=None):
        self._config, self._conn = config, None

    def __enter__(self):
        self._conn = connect(self._config)
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if self._conn is not None:  # always set (with-block only calls __exit__ after __enter__)
            self._conn.close()
        return False


def query(conn, sql, params=None):
    """Run a SELECT; return rows as a list of dicts. Fail-loud."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def execute(conn, sql, params=None):
    """Run a single write; return affected rowcount. Does not commit."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


class transaction:
    """Commit on clean exit, rollback on exception."""
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False
