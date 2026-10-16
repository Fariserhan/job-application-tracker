"""Shared fixtures. Every test is hermetic: data lives in pytest temp dirs, the network is
never touched, and the demo dataset is built once per session through the real pipeline."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jobtracker.config import Settings

DEMO_TODAY = date(2026, 10, 16)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", gmail_after="2000/01/01",
                    enrich_enabled=False, llm_enabled=False).ensure_dirs()


@pytest.fixture
def repo(settings):
    from jobtracker.store import Repository

    repository = Repository(settings)
    repository.init()
    return repository


@pytest.fixture(scope="session")
def demo(tmp_path_factory) -> Settings:
    """A fully built demo data dir (applications.db + warehouse.duckdb)."""
    from jobtracker.demo import build_demo

    target = tmp_path_factory.mktemp("demo") / "data"
    build_demo(target)
    return Settings(data_dir=target)


@pytest.fixture
def metrics(demo):
    from jobtracker.warehouse import connect
    from jobtracker.warehouse.metrics import Metrics

    with connect(demo) as con:
        yield Metrics(con, demo)


def meta(**overrides) -> dict:
    """Gmail metadata for one message (2026-03-02 10:00 UTC unless overridden)."""
    base = {
        "id": "m1", "thread_id": "t1", "internal_date": 1772445600000, "snippet": "",
        "from": "Acme Careers <careers@acme.com>",
        "subject": "Thank you for applying to Acme for Data Analyst",
        "date_header": "", "gmail_link": "https://mail.google.com/mail/u/0/#all/t1",
    }
    base.update(overrides)
    return base


def ms(day: str, hour: int = 10) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00:00").timestamp() * 1000)
