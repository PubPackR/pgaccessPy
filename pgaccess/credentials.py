"""Generic Postgres credential resolver for the pgaccess Python DB layer.

Ported from the crawler repos' CredentialManager with the house fixes:
- explicit DB_MODE (server|local), not sys.stdout.isatty() guessing (that guess
  is what breaks under subprocess);
- key dir / SSL cert resolved from config/env, not __file__, defaulting to the
  base-app CWD-relative ../../keys/... convention (see README "Path defaults");
- the DB secret (DB_MASTER_KEY + encrypted files) is kept separate from the GAM
  decrypt key (ADMANAGER_DECRYPT_KEY).

No AdManager references, no pandas: library-clean.
"""
import base64
import getpass
import hashlib
import json
import os

from cryptography.fernet import Fernet

DEFAULT_KEY_DIR = "../../keys/PostgreSQL_DB"
# Deliberately NOT under DEFAULT_KEY_DIR: on shiny.studyflix.info the encrypted
# credential files live in keys/PostgreSQL_DB/ but the RDS CA bundle sits in
# metabase-data/postgres/, which is where Billomatics::postgres_connect() points
# the R stack. The two really do live in different trees. Verified on the host
# 2026-08-04, after the previous keys/PostgreSQL_DB/ default failed to resolve.
DEFAULT_SSL_ROOT_CERT = "../../metabase-data/postgres/eu-central-1-bundle.pem"


class CredentialManager:
    def __init__(self, key_dir=None, ssl_root_cert=None, mode=None):
        self.key_dir = key_dir or os.getenv("DB_KEY_DIR", DEFAULT_KEY_DIR)
        self.ssl_root_cert = ssl_root_cert or os.getenv("DB_SSL_ROOT_CERT", DEFAULT_SSL_ROOT_CERT)
        self.mode = (mode or os.getenv("DB_MODE", "server")).lower()
        self.server_file = os.path.join(self.key_dir, "postgresql_server_python.txt")
        self.pass_file = os.path.join(self.key_dir, "postgresql_key_python.txt")

    def _fernet_key(self, user_password):
        return base64.urlsafe_b64encode(hashlib.sha256(user_password.encode()).digest())

    def _master_key(self):
        env_key = os.getenv("DB_MASTER_KEY")
        if env_key:
            return env_key
        raise RuntimeError("server mode: DB_MASTER_KEY not set in the environment")

    def get_db_config(self):
        if self.mode == "local":
            return {"type": "local", "host": os.getenv("DB_LOCAL_HOST", "localhost"),
                    "port": "5432", "database": "studyflix_local", "user": "postgres",
                    "password": getpass.getpass("local DB password: ").strip()}
        if self.mode != "server":
            raise RuntimeError(f"DB_MODE must be 'server' or 'local', got {self.mode!r}")
        if not os.path.exists(self.server_file) or not os.path.exists(self.pass_file):
            raise FileNotFoundError(f"server mode: key files missing in {self.key_dir}")
        cipher = Fernet(self._fernet_key(self._master_key()))
        with open(self.server_file, "rb") as f:
            server_info = json.loads(cipher.decrypt(f.read()).decode())
        with open(self.pass_file, "rb") as f:
            db_password = cipher.decrypt(f.read()).decode()
        return {"type": "server", "host": server_info["host"], "port": server_info["port"],
                "database": server_info["dbname"], "user": server_info["user"],
                "password": db_password, "sslmode": "verify-full",
                "sslrootcert": self.ssl_root_cert}
