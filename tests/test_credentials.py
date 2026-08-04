import json
import pytest
from cryptography.fernet import Fernet
from pgaccess.credentials import CredentialManager


def _fernet_from_master(master):
    import base64, hashlib
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master.encode()).digest()))


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
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setenv("DB_MASTER_KEY", "m")
    with pytest.raises(FileNotFoundError):
        CredentialManager(key_dir=str(tmp_path)).get_db_config()


def test_server_mode_master_key_unset_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.delenv("DB_MASTER_KEY", raising=False)
    (tmp_path / "postgresql_server_python.txt").write_bytes(b"x")
    (tmp_path / "postgresql_key_python.txt").write_bytes(b"x")
    with pytest.raises(RuntimeError):
        CredentialManager(key_dir=str(tmp_path)).get_db_config()


def test_server_mode_decrypts_and_builds_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_MODE", "server")
    monkeypatch.setenv("DB_MASTER_KEY", "master")
    cipher = _fernet_from_master("master")
    (tmp_path / "postgresql_server_python.txt").write_bytes(
        cipher.encrypt(json.dumps({"host": "h", "port": "5432", "dbname": "d", "user": "u"}).encode()))
    (tmp_path / "postgresql_key_python.txt").write_bytes(cipher.encrypt(b"secret"))
    cfg = CredentialManager(key_dir=str(tmp_path), ssl_root_cert="/c.pem").get_db_config()
    assert cfg["type"] == "server" and cfg["database"] == "d" and cfg["password"] == "secret"
    assert cfg["sslmode"] == "verify-full" and cfg["sslrootcert"] == "/c.pem"
