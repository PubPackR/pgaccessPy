import pytest
from pgaccess import database


class FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows, self.rowcount, self.executed = rows or [], rowcount, []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.executed.append((sql, params))
    def fetchall(self): return self._rows


class FakeConn:
    def __init__(self, cursor): self._cursor, self.committed, self.rolled_back = cursor, False, False
    def cursor(self, **kw): return self._cursor
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True
    def close(self): pass


def test_transaction_commits_on_success():
    conn = FakeConn(FakeCursor())
    with database.transaction(conn):
        pass
    assert conn.committed and not conn.rolled_back


def test_transaction_rolls_back_on_exception():
    conn = FakeConn(FakeCursor())
    with pytest.raises(ValueError):
        with database.transaction(conn):
            raise ValueError("boom")
    assert conn.rolled_back and not conn.committed


def test_query_returns_list_of_dicts():
    conn = FakeConn(FakeCursor(rows=[{"id": 1}, {"id": 2}]))
    assert database.query(conn, "SELECT 1") == [{"id": 1}, {"id": 2}]


def test_execute_returns_rowcount():
    conn = FakeConn(FakeCursor(rowcount=3))
    assert database.execute(conn, "UPDATE t SET x=%s", (1,)) == 3


def test_connect_strips_type_marker(monkeypatch):
    captured = {}
    monkeypatch.setattr(database.psycopg2, "connect", lambda **kw: captured.update(kw) or "CONN")
    assert database.connect({"type": "server", "host": "h", "password": "p"}) == "CONN"
    assert "type" not in captured and captured["host"] == "h"
