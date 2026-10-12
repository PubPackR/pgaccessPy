"""pgaccess -- authenticated PostgreSQL access for Studyflix Python base apps.

Two modules, deliberately no more:

    from pgaccess import database
    from pgaccess.credentials import CredentialManager

`credentials` resolves connection settings (through sfsecrets in server mode, an
interactive prompt in local mode) and shapes them for psycopg2. `database` opens
connections and runs queries against them. Nothing here knows about any
particular application.

sfsecrets is imported but deliberately not declared as a dependency: the
consuming app vendors it and puts it on sys.path. See `credentials` and README.

This is the Python twin of Billomatics' postgres_connect() -- not a
replacement for it. See README.
"""

__version__ = "0.2.0"
