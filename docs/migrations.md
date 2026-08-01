# Database Migrations (Alembic)

Echo's database schema is managed by **Alembic**, not auto-created at runtime.
This means schema changes (new columns, tables, indexes) are versioned, applied
deliberately, and never wipe your data.

## One-time: set up the database

With Postgres running (`docker compose up -d`) and your venv active:

    alembic upgrade head

This applies all migrations, creating the `reminders` and `events` tables (plus
an `alembic_version` table that tracks which migrations have run).

Run this once after cloning, and again whenever you pull new migrations.

## The everyday workflow — changing the schema

When you change a model in `src/echo/models.py` (add a column, a table, etc.):

1. **Autogenerate a migration** — Alembic diffs your models against the DB:

       alembic revision --autogenerate -m "add priority to reminders"

2. **Review the generated file** in `alembic/versions/`. Autogenerate is good
   but not perfect — check the `upgrade()` and `downgrade()` functions do what
   you expect. (It can miss things like column renames, seeing them as
   drop + add.)

3. **Apply it:**

       alembic upgrade head

Your existing data is preserved — only the schema changes.

## Useful commands

    alembic current              # which migration is the DB on?
    alembic history              # list all migrations
    alembic upgrade head         # apply everything pending
    alembic downgrade -1         # undo the last migration
    alembic downgrade base       # undo everything (empties schema)

## How it's wired

`alembic/env.py` pulls the database URL from `echo.config.settings` and the
schema from `echo.db.Base.metadata` — the same sources the app uses. So there's
no duplicated connection string, and autogenerate always compares against your
real models. `alembic.ini` intentionally leaves `sqlalchemy.url` blank because
`env.py` injects it.

## Note for tests

Tests don't use Alembic — they build the schema directly from model metadata for
speed (see `tests/conftest.py`). Alembic manages the *real* database only.

## If you change the database (e.g. reset Postgres)

After a `docker compose down -v` (which wipes the DB volume), the schema is gone.
Recreate it with:

    docker compose up -d
    alembic upgrade head