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

**Consumed by base-64** (`func/pgaccessPy` plus `func/_bootstrap.py` — Route B
below). base-65 still carries its own copies of both modules. Read this section
before wiring the next app — the deploy has constraints that are not obvious.

This package is not on PyPI, so `pip install pgaccess` will never work. There are
two possible routes, and neither is free.

#### Route A — install on the host from git

```sh
pip install git+https://github.com/PubPackR/pgaccessPy
```

Works without credentials because the repo is public. Needs no deploy changes at
all, and matches how every other Python dependency on `shiny.studyflix.info`
already exists: hand-installed and unmanaged.

The cost: every consuming app shares one installed version, so upgrading the
library upgrades all of them at once. Staged migrations are not possible.

#### Route B — git submodule

The package files travel inside the consuming repo, so nothing is installed on
the host. Each app pins its own commit, so apps can migrate one at a time.

The cost is three separate changes. The first has since been made; the other two
still apply to every new consumer.

1. **The shared deploy workflow must check out submodules.** Done:
   `studyflix/Shiny-0-studyflix-infrastructure/.github/workflows/deploy-app.yml`
   now checks out with `submodules: recursive`, so submodule directories arrive
   populated. It used to pass no `submodules:` input at all, and a consuming repo
   could not fix that itself — the checkout happens inside the shared workflow,
   which accepts only `shiny_host` / `shiny_port` / `shiny_username`.

2. **The submodule must live inside `func/`.** That deploy does not copy the
   repository; it copies a hardcoded allowlist of names — `app.R`, `config.R`,
   `config.yaml`, `func/`, `do/`, `one-off/`, `www/`, `Dockerfile`,
   `.dockerignore`, `requirements.txt`. A submodule at the repository root is
   never transferred, however correct the checkout is. Inside `func/` it rides
   along with a directory that is already copied.

3. **A `sys.path` bootstrap.** Nothing installs the package, so Python cannot
   find `pgaccess` on its own. The submodule's directory has to be prepended to
   `sys.path` before the first `from pgaccess import ...`.

#### Either way

The host runs no dependency installation step — deploys only rsync files, and
the `requirements.txt` in the allowlist above is copied but never installed. So
`psycopg2` must already be hand-installed on the host, and so must everything
sfsecrets imports: `cryptography`, `google-auth`, `requests`, `urllib3`. The
versions pinned in this `pyproject.toml` and in sfsecrets' own are the versions
confirmed installed there.

**sfsecrets travels separately.** `pgaccess.credentials` imports it but
deliberately does not declare it as a dependency (see Configuration below), so a
consuming app vendors the two submodules side by side — base-64 has
`func/pgaccessPy` and will have `func/sfsecrets` — and puts both on `sys.path`
before the first `from pgaccess import ...`.

## Configuration

All settings come from environment variables. Nothing is hardcoded per host.

| Variable | Default | Meaning |
|---|---|---|
| `DB_MODE` | `server` | `server` (resolve the credential through sfsecrets) or `local` (interactive password prompt) |
| `SF_SECRET_BACKEND` | `file` | Read by sfsecrets, not by this package. `gsm` for Google Secret Manager, `file` for the legacy encrypted keyfiles. Production sets `gsm` and sfsecrets refuses anything else there. |
| `DB_MASTER_KEY` | — | **The `file` backend only.** Decrypts the legacy keyfiles; unused under `gsm`. |
| `DB_KEY_DIR` | `../../keys/PostgreSQL_DB` | Directory holding the legacy encrypted keyfiles. The `file` backend only. |
| `DB_SSL_ROOT_CERT` | `../../metabase-data/postgres/eu-central-1-bundle.pem` | RDS CA bundle, verified against in server mode (`sslmode=verify-full`) |
| `DB_LOCAL_HOST` | `localhost` | Local mode only |

### Credential resolution

Server mode asks sfsecrets for one secret by name,
`studyflix-postgresql-connection`, and never learns which backend answered:
Google Secret Manager in production, the legacy encrypted keyfiles anywhere
else. What stays in this package is the database shaping —
`sslmode=verify-full`, the certificate path, psycopg2's keyword names. A secret
that is not a JSON object carrying `host`, `port`, `dbname`, `user` and
`password` is refused by field name, never by value.

`DB_MASTER_KEY` is deliberately **separate** from `ADMANAGER_DECRYPT_KEY` and any
other application secret. This package only ever knows about the database.

### Path defaults

The two path defaults are **relative to the process working directory**, not to
this package's location on disk. They resolve correctly only when the process
runs from the root of a base-app repo laid out as a sibling of `base-data`,
`keys` and `metabase-data`:

```
~/base-apps/<repo>/          <- process CWD
~/keys/PostgreSQL_DB/        <- ../../keys/PostgreSQL_DB       (encrypted credentials)
~/metabase-data/postgres/    <- ../../metabase-data/postgres   (RDS CA bundle)
```

**The credentials and the certificate are in different trees**, which is easy to
get wrong: the bundle is *not* under `keys/PostgreSQL_DB/`. It sits where
`Billomatics::postgres_connect()` expects it, so the R and Python stacks read the
same file. An earlier version of this package defaulted the certificate to the
keys directory; that path does not exist on the host and every server-mode
connection failed on it.

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
