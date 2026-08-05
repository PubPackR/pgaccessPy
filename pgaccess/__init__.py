"""pgaccess -- authenticated PostgreSQL access for Studyflix Python base apps.

Two modules, deliberately no more:

    from pgaccess import database
    from pgaccess.credentials import CredentialManager

`credentials` resolves connection settings (encrypted keyfiles in server mode,
an interactive prompt in local mode). `database` opens connections and runs
queries against them. Nothing here knows about any particular application.

This is the Python twin of Billomatics' postgres_connect() -- not a
replacement for it. See README.
"""

__version__ = "0.1.1"
