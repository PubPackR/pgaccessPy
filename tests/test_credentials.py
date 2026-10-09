import importlib
import json
import os
import sys

import pytest
import sfsecrets
from cryptography.fernet import Fernet

import pgaccess.credentials as credentials
from pgaccess.credentials import SECRET_NAME, CredentialManager

# A value that no error message in this module may ever echo.
SENTINEL = "canary-value-that-must-never-be-printed"


def _fernet_from_master(master):
    import base64, hashlib
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master.encode()).digest()))


def _write_legacy_keyfiles(tmp_path, master, password="secret"):
    """Write the two encrypted files sfsecrets' file backend reads, in the layout
    it expects, and return the key_dir to hand CredentialManager.

    The files sit under a PostgreSQL_DB/ subdirectory of tmp_path, not tmp_path
    itself: the backend resolves this secret from os.path.dirname(key_dir) joined
    with "PostgreSQL_DB/<file>" (sfsecrets._legacy_map.FILES), the same shape as
    the real DEFAULT_KEY_DIR ("../../keys/PostgreSQL_DB"). A flat tmp_path --
    this suite's shape while the decryption lived in credentials.py -- no longer
    resolves, because nothing but that fixed suffix is ever tried.
    """
    cipher = _fernet_from_master(master)
    key_dir = tmp_path / "PostgreSQL_DB"
    key_dir.mkdir()
    (key_dir / "postgresql_server_python.txt").write_bytes(
        cipher.encrypt(json.dumps(
            {"host": "h", "port": "5432", "dbname": "d", "user": "u"}).encode()))
    (key_dir / "postgresql_key_python.txt").write_bytes(cipher.encrypt(password.encode()))
    return str(key_dir)


@pytest.fixture(autouse=True)
def _isolated_secrets_environment(monkeypatch):
    """Pin the two pieces of ambient state sfsecrets reads, and clear its cache.

    SF_SECRET_BACKEND picks the backend; GOOGLE_APPLICATION_CREDENTIALS pointing
    at an existing file makes sfsecrets.is_production() true, and a production
    host refuses every backend but gsm. On a developer machine authenticated for
    Secret Manager -- which anyone working on this migration is -- that would
    fail every file-backend test below with "this host is in production mode"
    instead of testing what the test is named after.

    The cache is keyed by (name, version, key_dir, key digest) and lives for the
    process. tmp_path keeps today's tests apart by itself, but any shared
    key_dir would hand one test's credential to the next with no error.
    """
    monkeypatch.delenv("SF_SECRET_BACKEND", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    sfsecrets.secret_cache_clear()
    yield
    sfsecrets.secret_cache_clear()


def test_local_mode_returns_local_config(monkeypatch):
    monkeypatch.setenv("DB_MODE", "local")
    monkeypatch.setenv("DB_LOCAL_HOST", "myhost")
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "pw ")
    cfg = CredentialManager().get_db_config()
    assert cfg["type"] == "local" and cfg["host"] == "myhost" and cfg["password"] == "pw"


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("DB_MODE", "banana")
    with pytest.raises(RuntimeError):
        CredentialManager().get_db_config()


def test_server_mode_missing_files_raises(monkeypatch, tmp_path):
    """The error now comes from sfsecrets' file backend, not from this module.
    credentials.py deliberately no longer pre-checks that the keyfiles exist --
    that check was blind to the backend and would have made GSM-backed runs
    depend on legacy files they never read."""
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setenv("DB_MASTER_KEY", "m")
    with pytest.raises(FileNotFoundError):
        CredentialManager(key_dir=str(tmp_path / "PostgreSQL_DB")).get_db_config()


def test_server_mode_master_key_unset_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.delenv("DB_MASTER_KEY", raising=False)
    key_dir = _write_legacy_keyfiles(tmp_path, "master")
    with pytest.raises(RuntimeError):
        CredentialManager(key_dir=key_dir).get_db_config()


def test_default_ssl_root_cert_points_at_the_rds_bundle(monkeypatch, tmp_path):
    """Asserted as a literal so changing it has to be deliberate. The default
    used to be ../../keys/PostgreSQL_DB/eu-central-1-bundle.pem, which does not
    exist on shiny.studyflix.info -- every server-mode connect failed on it."""
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setenv("DB_MASTER_KEY", "master")
    monkeypatch.delenv("DB_SSL_ROOT_CERT", raising=False)
    key_dir = _write_legacy_keyfiles(tmp_path, "master")
    cfg = CredentialManager(key_dir=key_dir).get_db_config()
    assert cfg["sslrootcert"] == "../../metabase-data/postgres/eu-central-1-bundle.pem"


def test_server_mode_resolves_through_sfsecrets_and_builds_config(monkeypatch, tmp_path):
    """End to end against the real sfsecrets file backend: the fixture encrypts,
    sfsecrets decrypts and joins the two legacy files into one JSON object, and
    this module shapes it into psycopg2's keywords."""
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setenv("DB_MASTER_KEY", "master")
    key_dir = _write_legacy_keyfiles(tmp_path, "master")
    cfg = CredentialManager(key_dir=key_dir, ssl_root_cert="/c.pem").get_db_config()
    assert cfg["type"] == "server" and cfg["database"] == "d" and cfg["password"] == "secret"
    assert cfg["sslmode"] == "verify-full" and cfg["sslrootcert"] == "/c.pem"


def _fake_secret_get(payload, seen=None):
    def secret_get(name, **kwargs):
        if seen is not None:
            seen["name"] = name
            seen.update(kwargs)
        return payload
    return secret_get


def test_the_key_dir_handed_to_sfsecrets_is_the_parent_of_the_postgres_dir(monkeypatch):
    """key_dir points at PostgreSQL_DB; sfsecrets' file map holds paths relative
    to keys/, so the parent is what it must receive. The trailing separator is
    the point: without the normpath() first, dirname() returns the path
    unchanged and the backend looks for PostgreSQL_DB/PostgreSQL_DB/."""
    seen = {}
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setattr(sfsecrets, "secret_get", _fake_secret_get(json.dumps(
        {"host": "h", "port": "5432", "dbname": "d", "user": "u", "password": "p"}), seen))
    CredentialManager(key_dir=os.path.join("/srv", "keys", "PostgreSQL_DB") + os.sep).get_db_config()
    assert seen["name"] == SECRET_NAME
    assert os.path.basename(seen["key_dir"]) == "keys"


def test_a_secret_that_is_not_json_is_refused_without_echoing_it(monkeypatch):
    """json.JSONDecodeError.doc holds the whole string that failed to parse --
    for this secret, the connection details and the password. The refusal must
    name neither, and must not leave the JSONDecodeError reachable through the
    exception chain, where a logger walking __context__ would find .doc."""
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setattr(sfsecrets, "secret_get", _fake_secret_get("not json: " + SENTINEL))
    with pytest.raises(RuntimeError) as excinfo:
        CredentialManager(key_dir="/nowhere/PostgreSQL_DB").get_db_config()
    assert SENTINEL not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_a_secret_that_is_not_an_object_is_refused(monkeypatch):
    """A bare JSON scalar parses fine and would then fail deep inside the
    shaping with a TypeError about subscripting an int."""
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setattr(sfsecrets, "secret_get", _fake_secret_get("123"))
    with pytest.raises(RuntimeError, match="not to the JSON object"):
        CredentialManager(key_dir="/nowhere/PostgreSQL_DB").get_db_config()


def test_a_secret_missing_a_field_names_the_field_and_no_value(monkeypatch):
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setattr(sfsecrets, "secret_get", _fake_secret_get(json.dumps(
        {"host": SENTINEL, "port": "5432", "dbname": "d", "user": "u"})))
    with pytest.raises(RuntimeError) as excinfo:
        CredentialManager(key_dir="/nowhere/PostgreSQL_DB").get_db_config()
    assert "password" in str(excinfo.value)
    assert SENTINEL not in str(excinfo.value)


def test_a_missing_sfsecrets_names_the_vendoring_contract(monkeypatch):
    """sfsecrets is not a dependency of this package: the consuming app vendors
    it as a git submodule and puts it on sys.path (base-64's func/_bootstrap.py).
    That contract is implicit, so this pins the error an app gets when it forgets
    -- a bare "No module named 'sfsecrets'" would leave whoever hits it guessing.

    Setting sys.modules["sfsecrets"] = None is what makes `import sfsecrets`
    fail; the reload at the end puts the module the other tests use back.
    """
    monkeypatch.setitem(sys.modules, "sfsecrets", None)
    try:
        with pytest.raises(ImportError) as excinfo:
            importlib.reload(credentials)
        message = str(excinfo.value)
        assert "sfsecrets" in message and "vendor" in message
    finally:
        monkeypatch.undo()
        importlib.reload(credentials)
