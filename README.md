# pgaccessPy

Authenticated PostgreSQL access for Studyflix Python base apps. Two modules and
nothing else: resolve credentials, open a connection, run queries in a real
transaction.

```python
from pgaccess import database

with database.connection() as conn:          # credentials resolved from the environment
    with database.transaction(conn):         # commit on success, rollback on exception
        database.execute(conn, "UPDATE processed.some_table SET x = %s WHERE id = %s", (1, 42))
    rows = database.query(conn, "SELECT * FROM processed.some_table WHERE id = %s", (42,))
```

`query` returns a list of plain dicts. `execute` returns a rowcount and does
**not** commit — that is `transaction`'s job. Errors are never swallowed: a
failing statement raises.

## Relationship to Billomatics (R)

This is the **Python twin** of `Billomatics::postgres_connect()` /
`postgres_upsert_data()`, not a replacement for them. R code — `prepare_shiny.R`,
`load.R`, everything else in the R stack — keeps using Billomatics. Neither
package supersedes the other; they cover the same job in two languages that
cannot share an implementation.

There is deliberately **no upsert layer here yet**. `upsert_dataframe` has to
match `Billomatics::postgres_upsert_data(delete_missing = FALSE)` semantics
exactly, and it will be proven inside a consuming app before it moves down into
this package.

## Installation

### Local development

```sh
uv sync
uv run pytest tests -v
```

### Production (base apps)

Production consumes this repo as a **git submodule**, not as a pip install. The
FlowForce host on `shiny.studyflix.info` has no dependency installation step —
deploys only rsync files — so `cryptography` and `psycopg2` must already be
hand-installed on the host. The versions pinned in `pyproject.toml` are the
versions confirmed installed there.

Because the package is imported from a checked-out submodule rather than
installed, the submodule's directory has to be on `sys.path` before the first
`from pgaccess import ...`. Two things to verify when wiring a consuming repo:

- its deploy workflow checks out submodules (`actions/checkout` with
  `submodules: recursive`) — otherwise the directory rsyncs to the host empty
  and every import fails at runtime;
- the rsync step does not exclude the submodule path.

## Configuration

All settings come from environment variables. Nothing is hardcoded per host.

| Variable | Default | Meaning |
|---|---|---|
| `DB_MODE` | `server` | `server` (encrypted keyfiles) or `local` (interactive password prompt) |
| `DB_MASTER_KEY` | — | **Required in server mode.** Decrypts the credential files. |
| `DB_KEY_DIR` | `../../keys/PostgreSQL_DB` | Directory holding the encrypted credential files |
| `DB_SSL_ROOT_CERT` | `../../keys/PostgreSQL_DB/eu-central-1-bundle.pem` | SSL root cert, verified against in server mode (`sslmode=verify-full`) |
| `DB_LOCAL_HOST` | `localhost` | Local mode only |

`DB_MASTER_KEY` is deliberately **separate** from `ADMANAGER_DECRYPT_KEY` and any
other application secret. This package only ever knows about the database.

### Path defaults

The two path defaults are **relative to the process working directory**, not to
this package's location on disk. They resolve correctly only when the process
runs from the root of a base-app repo laid out as a sibling of `base-data` and
`keys`:

```
~/base-apps/<repo>/     <- process CWD
~/keys/PostgreSQL_DB/   <- reached as ../../keys/PostgreSQL_DB
```

Anywhere else — a different layout, a test runner, a container — set `DB_KEY_DIR`
and `DB_SSL_ROOT_CERT` explicitly, or pass `key_dir` / `ssl_root_cert` to
`CredentialManager` directly. They are constructor arguments precisely so the
defaults never have to be fought.

## Provenance

These modules started life in the crawler repos as `CredentialManager` and were
copied between projects several times. The variant promoted into this package is
**base-65-admanager_api_control's**, because it carries fixes the older copies do
not:

- **Explicit `DB_MODE`** instead of guessing interactivity from
  `sys.stdout.isatty()`. The guess is wrong whenever the process is a subprocess
  — which is exactly how the R/Python pipeline in base-65 runs — and produced a
  silent fall-through to the wrong credential path.
- **Key directory and SSL certificate resolved from config/environment**, not
  from `__file__`. Deriving paths from the module's own location breaks the
  moment the module moves — as it just did, into this package.
- **Real transactions.** Connections are not put into autocommit at connect time;
  `transaction()` commits on clean exit and rolls back on exception.
- **Fail-loud.** No blanket exception handling; database errors propagate.

Anyone later reconciling the crawler repos against this package should treat
those four as the intended behaviour and the older copies as the ones to change.

## Tests

`tests/test_credentials.py` and `tests/test_database.py` use fakes and need no
database. `tests/test_database_integration.py` runs against a real Postgres and
skips itself unless `PGACCESS_IT_HOST` is set; CI provides a throwaway
`postgres:16` service container.

The `sslmode=verify-full` server path is **not** covered by tests — it needs the
production RDS certificate bundle and is not reproducible in CI.
