"""Shared test setup, including a throwaway Postgres database for tests that need one.

Tests using the `db` fixture get a database created for this run
(command_center_test_<pid>), migrated with the real Alembic migrations and
dropped at the end. They are skipped when Postgres isn't running.
"""

import os
from contextlib import nullcontext

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.sync import runner

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def no_db_sync_lock(monkeypatch):
    """Unit tests run without Postgres, so only the in-process sync lock is used."""
    monkeypatch.setattr(runner, "_db_lock", nullcontext)


@pytest.fixture(scope="module")
def pg_url():
    url = make_url(settings.DATABASE_URL)
    test_url = url.set(database=f"command_center_test_{os.getpid()}")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_url.database}"'))
            conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    except Exception as e:  # no local Postgres
        pytest.skip(f"Postgres not available: {type(e).__name__}")
    engine = create_engine(test_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    engine.dispose()
    yield test_url.render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_url.database}" WITH (FORCE)'))
    admin.dispose()


def migrate(url: str, revision: str, monkeypatch, down: bool = False) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", url)  # alembic/env.py reads it
    config = Config(os.path.join(BACKEND, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    (command.downgrade if down else command.upgrade)(config, revision)


@pytest.fixture
def db(pg_url, monkeypatch):
    """A session on the migrated test database; everything is rolled back afterwards."""
    migrate(pg_url, "head", monkeypatch)
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        trans = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield session
        session.close()
        trans.rollback()
    engine.dispose()
