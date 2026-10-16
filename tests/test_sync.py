"""Sync engine end to end over the synthetic mailbox (the production path, no network)."""
from __future__ import annotations

from datetime import date

from jobtracker.demo.mailbox import COMPANIES, ROLES, DemoMailbox
from jobtracker.store import Repository
from jobtracker.sync import SyncEngine, reparse_from_cache

TODAY = date(2026, 10, 16)


class CountingMailbox(DemoMailbox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.body_requests: list[str] = []
        self.fail_bodies: set[str] = set()

    def bodies(self, message_ids):
        self.body_requests.extend(message_ids)
        return {k: v for k, v in super().bodies(message_ids).items() if k not in self.fail_bodies}


def test_full_sync_parses_the_demo_inbox_perfectly(settings):
    box = CountingMailbox(today=TODAY, applications=60, days=90)
    summary = SyncEngine(settings, box, build_warehouse=False).run()
    repo = Repository(settings)
    rows = repo.applications()
    real = [r for r in rows if r["thread_id"].startswith("demo-thread")]
    noise = [r for r in rows if r["thread_id"].startswith("demo-noise")]
    assert all(r["is_valid"] for r in real) and not any(r["is_valid"] for r in noise)
    assert {r["company_name"] for r in real} <= {c[0] for c in COMPANIES}
    assert {r["role_title"] for r in real} <= {r[0] for r in ROLES}
    assert summary.found == len(box.messages)
    assert summary.inserted == len(rows)
    assert repo.load_state()["last_sync_epoch"]


def test_incremental_sync_only_downloads_new_mail(settings):
    box = CountingMailbox(today=TODAY, applications=30, days=60)
    engine = SyncEngine(settings, box, build_warehouse=False)
    engine.run()
    first = len(box.body_requests)
    box.body_requests.clear()
    summary = engine.run()
    assert first > 0
    # the overlap window re-queries recent mail, but cached messages are never re-downloaded
    assert box.body_requests == []
    assert summary.inserted == 0


def test_full_reparse_reads_bodies_from_cache(settings):
    box = CountingMailbox(today=TODAY, applications=20, days=40)
    engine = SyncEngine(settings, box, build_warehouse=False)
    engine.run()
    box.body_requests.clear()
    summary = engine.run(full=True)
    assert box.body_requests == []
    assert summary.bodies_from_cache > 0


def test_failed_body_fetch_is_retried_next_sync(settings):
    box = CountingMailbox(today=TODAY, applications=10, days=20)
    target = next(m.id for m in box.messages if m.thread_id.startswith("demo-thread"))
    box.fail_bodies = {target}
    engine = SyncEngine(settings, box, build_warehouse=False)
    engine.run()
    repo = Repository(settings)
    assert target in repo.filter_unprocessed([target])
    box.fail_bodies = set()
    engine.run(full=True)
    assert repo.filter_unprocessed([target]) == []


def test_sync_builds_the_warehouse(settings):
    summary = SyncEngine(settings, DemoMailbox(today=TODAY, applications=15, days=30)).run()
    assert summary.warehouse_rows > 0
    assert settings.warehouse_path.exists()


def test_progress_callback_reports_every_stage(settings):
    stages = []
    SyncEngine(settings, DemoMailbox(today=TODAY, applications=5, days=10),
               build_warehouse=False).run(progress=lambda s, m, f: stages.append((s, f)))
    names = [s for s, _ in stages]
    assert names[0] == "search" and names[-1] == "done"
    assert [f for _, f in stages] == sorted(f for _, f in stages)


def test_offline_reparse_from_cache(settings):
    SyncEngine(settings, DemoMailbox(today=TODAY, applications=10, days=20),
               build_warehouse=False).run()
    assert reparse_from_cache(settings) >= 0
