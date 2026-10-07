"""Shared test setup."""

from contextlib import nullcontext

import pytest

from app.sync import runner


@pytest.fixture(autouse=True)
def no_db_sync_lock(monkeypatch):
    """Unit tests run without Postgres, so only the in-process sync lock is used."""
    monkeypatch.setattr(runner, "_db_lock", nullcontext)
