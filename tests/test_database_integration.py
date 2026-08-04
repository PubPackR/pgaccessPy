"""Round-trip tests against a real Postgres.

Skipped unless PGACCESS_IT_HOST is set. CI provides a throwaway postgres:16
service container (see .github/workflows/ci.yml); locally you can point these
at any scratch database:

    PGACCESS_IT_HOST=localhost PGACCESS_IT_PASSWORD=postgres uv run pytest tests

These cover what the fake-based tests in test_database.py cannot: that the
psycopg2 kwargs we build are actually accepted, that RealDictCursor really
yields dicts, and that commit/rollback move real rows. The server-mode
sslmode=verify-full path is NOT covered -- that needs the RDS cert bundle and
is not reproducible in CI.
"""
import os
import uuid

import pytest

from pgaccess import database

pytestmark = pytest.mark.skipif(
    not os.getenv("PGACCESS_IT_HOST"),
    reason="integration tests need a throwaway Postgres (set PGACCESS_IT_HOST)",
)


@pytest.fixture
def config():
    """A config dict shaped exactly like CredentialManager.get_db_config()
    returns, including the 'type' marker connect() has to strip."""
    return {
        "type": "server",
        "host": os.environ["PGACCESS_IT_HOST"],
        "port": os.getenv("PGACCESS_IT_PORT", "5432"),
        "database": os.getenv("PGACCESS_IT_DB", "postgres"),
        "user": os.getenv("PGACCESS_IT_USER", "postgres"),
        "password": os.getenv("PGACCESS_IT_PASSWORD", "postgres"),
    }


@pytest.fixture
def table(config):
    """A scratch table dropped again afterwards."""
    name = f"pgaccess_it_{uuid.uuid4().hex[:12]}"
    conn = database.connect(config)
    try:
        with database.transaction(conn):
            database.execute(conn, f"CREATE TABLE {name} (id int primary key, label text)")
        yield name
        with database.transaction(conn):
            database.execute(conn, f"DROP TABLE IF EXISTS {name}")
    finally:
        database.close(conn)


def test_connect_accepts_a_real_credential_config(config):
    """The 'type' marker is stripped rather than handed to psycopg2."""
    conn = database.connect(config)
    try:
        assert database.query(conn, "SELECT 1 AS one") == [{"one": 1}]
    finally:
        database.close(conn)


def test_connection_context_manager_closes(config):
    with database.connection(config) as conn:
        assert database.query(conn, "SELECT 1 AS one") == [{"one": 1}]
    assert conn.closed


def test_connect_does_not_autocommit(config, table):
    """A write outside transaction() is not visible to a second connection."""
    writer = database.connect(config)
    reader = database.connect(config)
    try:
        database.execute(writer, f"INSERT INTO {table} VALUES (%s, %s)", (1, "uncommitted"))
        assert database.query(reader, f"SELECT * FROM {table}") == []
    finally:
        writer.rollback()
        database.close(writer)
        database.close(reader)


def test_transaction_commit_persists_rows(config, table):
    with database.connection(config) as conn:
        with database.transaction(conn):
            rowcount = database.execute(
                conn, f"INSERT INTO {table} VALUES (%s, %s), (%s, %s)", (1, "a", 2, "b"))
        assert rowcount == 2

    with database.connection(config) as conn:
        rows = database.query(conn, f"SELECT id, label FROM {table} ORDER BY id")
    assert rows == [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}]


def test_transaction_rollback_discards_rows(config, table):
    with database.connection(config) as conn:
        with pytest.raises(ValueError):
            with database.transaction(conn):
                database.execute(conn, f"INSERT INTO {table} VALUES (%s, %s)", (9, "gone"))
                raise ValueError("boom")

    with database.connection(config) as conn:
        assert database.query(conn, f"SELECT * FROM {table}") == []


def test_query_returns_plain_dicts(config, table):
    with database.connection(config) as conn:
        with database.transaction(conn):
            database.execute(conn, f"INSERT INTO {table} VALUES (%s, %s)", (1, "a"))
        rows = database.query(conn, f"SELECT id, label FROM {table}")
    assert type(rows[0]) is dict  # RealDictRow is a dict subclass -- we hand back plain dicts


def test_execute_errors_propagate(config, table):
    """Fail-loud: psycopg2 errors are not swallowed."""
    import psycopg2
    with database.connection(config) as conn:
        with pytest.raises(psycopg2.Error):
            database.execute(conn, f"INSERT INTO {table} (nope) VALUES (1)")
