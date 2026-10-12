"""Generic Postgres credential resolver for the pgaccess Python DB layer.

Ported from the crawler repos' CredentialManager with the house fixes:
- explicit DB_MODE (server|local), not sys.stdout.isatty() guessing (that guess
  is what breaks under subprocess);
- key dir / SSL cert resolved from config/env, not __file__, defaulting to the
  base-app CWD-relative ../../keys/... convention (see README "Path defaults").

No AdManager references, no pandas: library-clean.

Resolving the server-mode credential now belongs to sfsecrets. This module asks
for it by NAME and never learns which backend answered -- Google Secret Manager
in production, the legacy encrypted keyfiles anywhere else. What stays here is
the DB shaping: a secrets package has no business knowing what sslmode is.

sfsecrets is deliberately NOT a dependency of this package. Consuming apps
vendor it as a git submodule and put it on sys.path before importing pgaccess
(base-64: func/sfsecrets plus func/_bootstrap.py), because the FlowForce host
runs no dependency-installation step -- the deploy only rsyncs files. That makes
the requirement implicit, so the import below states it when it fails and
tests/test_credentials.py pins that message.
"""
import getpass
import json
import os

try:
    import sfsecrets
except ImportError as exc:
    if exc.name != "sfsecrets":
        # Something sfsecrets itself imports is missing -- cryptography, say,
        # which this package deliberately stopped declaring. Re-raise it as
        # itself: an error must not send an operator to the wrong file, which is
        # the same reason sfsecrets has _key_source().
        raise
    raise ImportError(
        "pgaccess.credentials resolves credentials through sfsecrets, which is "
        "not a dependency of this package: the consuming app vendors it as a git "
        "submodule and puts it on sys.path first (base-64's func/_bootstrap.py "
        "is the pattern). The import error that got us here is chained below."
    ) from exc

if not hasattr(sfsecrets, "secret_get"):
    # A directory on sys.path named sfsecrets but holding no module imports as an
    # implicit namespace package: the import above SUCCEEDS and the first
    # resolution dies on AttributeError instead. That is what an uninitialised
    # submodule looks like to any consumer that puts the containing directory on
    # sys.path rather than the submodule itself. No consumer does that today
    # (base-64 adds func/pgaccessPy, base-65 adds func/sfsecrets, and an empty
    # submodule then fails cleanly above), so this is a guard against a wiring
    # mistake, not against today's layout.
    raise ImportError(
        "sfsecrets imported but has no secret_get(): %r. An empty directory on "
        "sys.path imports as a namespace package, which is what an "
        "uninitialised submodule looks like -- run `git submodule update --init "
        "--recursive` in the consuming app." % getattr(sfsecrets, "__file__", None))

SECRET_NAME = "studyflix-postgresql-connection"
# Field names, never values, appear in this module's error messages.
REQUIRED_FIELDS = ("host", "port", "dbname", "user", "password")

DEFAULT_KEY_DIR = "../../keys/PostgreSQL_DB"
# Deliberately NOT under DEFAULT_KEY_DIR: on shiny.studyflix.info the encrypted
# credential files live in keys/PostgreSQL_DB/ but the RDS CA bundle sits in
# metabase-data/postgres/, which is where Billomatics::postgres_connect() points
# the R stack. The two really do live in different trees. Verified on the host
# 2026-08-04, after the previous keys/PostgreSQL_DB/ default failed to resolve.
DEFAULT_SSL_ROOT_CERT = "../../metabase-data/postgres/eu-central-1-bundle.pem"

_UNSET = object()  # marks "no value produced" without attaching implicit
                   # exception context -- see _resolve_secret.


class CredentialManager:
    def __init__(self, key_dir=None, ssl_root_cert=None, mode=None):
        self.key_dir = key_dir or os.getenv("DB_KEY_DIR", DEFAULT_KEY_DIR)
        self.ssl_root_cert = ssl_root_cert or os.getenv("DB_SSL_ROOT_CERT", DEFAULT_SSL_ROOT_CERT)
        self.mode = (mode or os.getenv("DB_MODE", "server")).lower()

    def _resolve_secret(self):
        """The secret as a dict, or a RuntimeError that names no value.

        No pre-check that the keyfiles exist. The version of this method that
        decrypted them had one, and keeping it would have made every GSM-backed
        run depend on legacy files it never reads -- green today only because
        keys/ still exists on the host, and a landmine the day it is deleted.
        Missing files are the file backend's business and it says so itself:
        FileNotFoundError("<path> not found for <name>").

        key_dir is passed EXPLICITLY rather than through the environment, so two
        managers with different key directories in one process cannot silently
        share one (sfsecrets.secret_get's docstring has the review finding).
        self.key_dir points AT PostgreSQL_DB and sfsecrets' file map holds paths
        relative to keys/, so the parent is what the backend wants -- computed
        here, where self.key_dir's shape is known, rather than assumed inside
        the package. normpath first: dirname() on a path with a trailing
        separator (".../PostgreSQL_DB/") returns it unchanged instead of its
        parent, and normpath collapses the separator so dirname finds the real
        one rather than doubling PostgreSQL_DB onto itself.

        `or os.curdir` because dirname() returns "" for a single-component
        key_dir ("PostgreSQL_DB", or "./PostgreSQL_DB" -- normpath strips the
        "."), and "" is falsy in sfsecrets' `root = key_dir or SF_KEY_DIR or
        "../../keys"`. The explicit argument would be dropped and the real keys
        directory read instead, silently and with a different credential --
        exactly the failure that making key_dir an argument exists to prevent,
        reopened one layer up. "." keeps the caller in its own directory.
        """
        parent = os.path.dirname(os.path.normpath(self.key_dir)) or os.curdir
        raw = sfsecrets.secret_get(SECRET_NAME, key_dir=parent)

        fields = _UNSET
        parse_error = None
        try:
            fields = json.loads(raw)
        except ValueError as exc:
            # json.JSONDecodeError sets .doc to the string it failed to parse --
            # here the WHOLE secret, password included. str(exc) is the
            # "Expecting value: line 1 column 1" form and carries none of it, so
            # it is safe to keep; it is captured here because the raise happens
            # below, OUTSIDE this block. `from None` would not be enough: it
            # clears __cause__ and sets __suppress_context__, but Python still
            # attaches __context__ to any raise executed lexically inside an
            # except block, so a logger walking __context__ directly
            # (Sentry-style capture, structured JSON logging) would reach .doc.
            parse_error = str(exc)
        if fields is _UNSET:
            raise RuntimeError(
                "%s did not parse as JSON (%s). Its value is withheld from this "
                "message on purpose." % (SECRET_NAME, parse_error))
        if not isinstance(fields, dict):
            raise RuntimeError(
                "%s decoded to %s, not to the JSON object this expects."
                % (SECRET_NAME, type(fields).__name__))
        missing = [f for f in REQUIRED_FIELDS if f not in fields]
        if missing:
            raise RuntimeError(
                "%s is missing the field(s): %s."
                % (SECRET_NAME, ", ".join(missing)))
        return fields

    def get_db_config(self):
        if self.mode == "local":
            return {"type": "local", "host": os.getenv("DB_LOCAL_HOST", "localhost"),
                    "port": "5432", "database": "studyflix_local", "user": "postgres",
                    "password": getpass.getpass("local DB password: ").strip()}
        if self.mode != "server":
            raise RuntimeError(f"DB_MODE must be 'server' or 'local', got {self.mode!r}")
        fields = self._resolve_secret()
        return {"type": "server", "host": fields["host"], "port": fields["port"],
                "database": fields["dbname"], "user": fields["user"],
                "password": fields["password"], "sslmode": "verify-full",
                "sslrootcert": self.ssl_root_cert}
