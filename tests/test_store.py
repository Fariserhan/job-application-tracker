"""Operational store: merge rules, event log, manual edits, v1 -> v2 migration."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from jobtracker import domain
from jobtracker.parsing import parse_message
from jobtracker.store import SCHEMA_VERSION, Repository
from tests.conftest import meta, ms


def apply(repo, body: str = "", **overrides) -> str:
    return repo.apply_message(parse_message(meta(**overrides), body))


def statuses(repo, thread="t1"):
    return [(e["from_status"], e["to_status"], e["occurred_on"], e["source"])
            for e in repo.events(thread)]


def test_first_confirmation_inserts_with_opening_event(repo):
    assert apply(repo, internal_date=ms("2026-03-02")) == "inserted"
    row = repo.get("t1")
    assert (row["company_name"], row["role_title"], row["current_status"]) == \
        ("Acme", "Data Analyst", "Applied")
    assert row["application_date"] == "2026-03-02"
    assert statuses(repo) == [(None, "Applied", "2026-03-02", "gmail")]


def test_rejection_is_dated_by_the_email_not_the_sync(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    apply(repo, id="m2", internal_date=ms("2026-03-20"),
          subject="Update on your application",
          snippet="We regret to inform you that we will not be moving forward.")
    row = repo.get("t1")
    assert row["current_status"] == "Rejected"
    assert row["last_updated"] == "2026-03-20"
    assert statuses(repo)[-1] == ("Applied", "Rejected", "2026-03-20", "gmail")


def test_older_email_never_rolls_status_back(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    apply(repo, id="m3", internal_date=ms("2026-03-15"), subject="Interview invitation",
          snippet="We would like to invite you to an interview next week.")
    apply(repo, id="m2", internal_date=ms("2026-03-05"), subject="Online assessment",
          snippet="Please complete the online assessment.")
    assert repo.get("t1")["current_status"] == "Interview"


def test_generic_follow_up_never_demotes_an_interview(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    apply(repo, id="m2", internal_date=ms("2026-03-10"), subject="Interview invitation",
          snippet="We would like to invite you to an interview.")
    apply(repo, id="m3", internal_date=ms("2026-03-12"),
          subject="Thank you for applying to Acme for Data Analyst")
    assert repo.get("t1")["current_status"] == "Interview"


def test_validity_is_an_or_across_the_thread(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    apply(repo, id="m2", internal_date=ms("2026-03-03"), subject="Job alert: 20 new jobs",
          **{"from": "jobalerts@linkedin.com"})
    assert repo.get("t1")["is_valid"] == 1


def test_platform_name_never_replaces_a_real_employer(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    apply(repo, id="m2", internal_date=ms("2026-03-04"),
          **{"from": "LinkedIn <jobs-noreply@linkedin.com>"},
          subject="Your application was viewed")
    assert repo.get("t1")["company_name"] == "Acme"


def test_ghosting_is_dated_when_the_threshold_was_crossed(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    assert repo.apply_ghosting(21, today="2026-05-01") == 1
    row = repo.get("t1")
    assert row["current_status"] == "Ghosted"
    assert statuses(repo)[-1] == ("Applied", "Ghosted", "2026-03-23", "system")


def test_new_email_revives_a_ghosted_thread(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    repo.apply_ghosting(21, today="2026-05-01")
    apply(repo, id="m2", internal_date=ms("2026-05-02"), subject="Interview invitation",
          snippet="We would like to invite you to an interview.")
    assert repo.get("t1")["current_status"] == "Interview"


def test_manual_noise_verdict_is_sticky(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    repo.set_valid("t1", False, "not a real application")
    apply(repo, id="m2", internal_date=ms("2026-03-03"))
    row = repo.get("t1")
    assert row["is_valid"] == 0 and row["filter_reason"].startswith("manual")


def test_manual_status_and_follow_up(repo):
    apply(repo, internal_date=ms("2026-03-02"))
    assert repo.set_status("t1", "Interview", note="phone call from HR")
    assert statuses(repo)[-1][1:] == ("Interview", date.today().isoformat(), "manual")
    assert repo.record_follow_up("t1", "emailed the recruiter")
    row = repo.get("t1")
    assert "Followed up: emailed the recruiter" in row["notes"]
    assert row["last_updated"] == date.today().isoformat()


def test_manual_application_and_delete(repo):
    tid = repo.add_manual_application("Kasturi Analytics", "Data Analyst", "Company Site",
                                      applied_on="2026-04-01", status="Interview")
    row = repo.get(tid)
    assert row["origin"] == "manual" and row["role_family"] == "Data & Analytics"
    assert [e["to_status"] for e in repo.events(tid)] == ["Applied", "Interview"]
    assert repo.delete_application(tid)
    assert repo.get(tid) is None and repo.events(tid) == []


def test_message_cache_keeps_bodies_and_verdicts(repo):
    record = parse_message(meta(), "Thank you for applying. We received your application.")
    repo.apply_message(record)
    repo.mark_processed([record], {"m1": "body text"})
    assert repo.filter_unprocessed(["m1", "m2"]) == ["m2"]
    assert repo.cached_bodies(["m1"]) == {"m1": "body text"}
    # a later call without a body must not blank the cached one
    repo.mark_processed([record], {})
    assert repo.cached_bodies(["m1"]) == {"m1": "body text"}


def test_backup_rotation(repo, settings):
    for i in range(18):
        (settings.backups_dir / f"20260101-0000{i:02d}-old").mkdir(parents=True)
    folder = repo.backup("manual")
    assert (folder / "applications.db").exists()
    assert len(list(settings.backups_dir.iterdir())) == 15


# --------------------------------------------------------------------------- migration

V1_SCHEMA = """
CREATE TABLE applications (
    thread_id TEXT PRIMARY KEY, company_name TEXT NOT NULL, role_title TEXT NOT NULL,
    source_platform TEXT DEFAULT 'Other', application_date TEXT NOT NULL,
    current_status TEXT NOT NULL, last_updated TEXT NOT NULL,
    job_description_snippet TEXT DEFAULT '', job_url TEXT DEFAULT '', gmail_link TEXT DEFAULT '',
    latest_subject TEXT DEFAULT '', is_valid INTEGER NOT NULL DEFAULT 1,
    filter_reason TEXT DEFAULT '', job_type TEXT DEFAULT '', seniority_level TEXT DEFAULT '',
    industry TEXT DEFAULT '', notes TEXT DEFAULT '');
CREATE TABLE status_history (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
    from_status TEXT, to_status TEXT, note TEXT DEFAULT '', changed_at TEXT);
CREATE TABLE processed_messages (message_id TEXT PRIMARY KEY, thread_id TEXT,
    processed_at TEXT, body_text TEXT DEFAULT '', message_date TEXT DEFAULT '');
CREATE TABLE discovered_jobs (job_key TEXT PRIMARY KEY, title TEXT NOT NULL);
"""


def test_v1_database_migrates_in_place_after_a_backup(settings):
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(V1_SCHEMA)
        conn.executemany(
            "INSERT INTO applications (thread_id, company_name, role_title, source_platform, "
            "application_date, current_status, last_updated, notes) VALUES (?,?,?,?,?,?,?,?)",
            [("a", "Acme", "Pricing Analyst", "LinkedIn", "2026-02-01", "Screening/OA",
              "2026-02-10", "my note"),
             ("b", "Beta", "Data Analyst", "JobStreet", "2026-02-03", "Applied",
              "2026-02-03", ""),
             ("c", "Gamma", "BI Analyst", "Direct ATS", "2026-02-04", "Rejected",
              "2026-02-20", "")])
        conn.execute("INSERT INTO status_history (thread_id, from_status, to_status, note, "
                     "changed_at) VALUES ('c', 'Applied', 'Rejected', 'updated from Gmail', "
                     "'2026-02-21 09:00:00')")
    repo = Repository(settings)
    assert repo.init() == SCHEMA_VERSION
    assert any("pre-v2-migration" in p.name for p in settings.backups_dir.iterdir())

    a = repo.get("a")
    assert a["current_status"] == domain.ASSESSMENT          # legacy alias normalised
    assert a["notes"] == "my note"                           # user data preserved
    assert a["role_family"] == "Actuarial"
    assert [e["to_status"] for e in repo.events("a")] == ["Applied", "Assessment / OA"]
    assert {e["source"] for e in repo.events("a")} == {"inferred"}
    c_events = repo.events("c")
    assert ("Applied", "Rejected", "legacy") in {(e["from_status"], e["to_status"], e["source"])
                                                for e in c_events}
    assert not any(e["source"] == "inferred" and e["to_status"] == "Rejected" for e in c_events)
    with repo.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "discovered_jobs" not in tables and "status_history" in tables
    # migrating twice is a no-op
    before = len(repo.events("a"))
    repo.init()
    assert len(repo.events("a")) == before


def test_ghosting_respects_threshold(repo):
    day = (date.today() - timedelta(days=10)).isoformat()
    repo.add_manual_application("Delta", "Risk Analyst", applied_on=day)
    assert repo.apply_ghosting(21) == 0
    assert repo.apply_ghosting(7) == 1


def test_messy_v1_database_builds_a_clean_warehouse(settings):
    """Real v1 databases carry legacy statuses, blank dates and note-only history rows."""
    from jobtracker.warehouse import build, manifest

    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(V1_SCHEMA)
        conn.executemany(
            "INSERT INTO applications (thread_id, company_name, role_title, application_date, "
            "current_status, last_updated, is_valid) VALUES (?,?,?,?,?,?,?)",
            [("a", "Acme", "Data Analyst", "2026-02-01 09:30:00", "Response", "", 1),
             ("b", "Beta", "", "2026-02-02", "Ghosted/Inactive", "2026-03-01", 1),
             ("c", "Gamma", "Analyst", "2026-02-03", "Weird Legacy Status", "2026-02-03", 1),
             ("d", "Noise Co", "x", "2026-02-04", "Applied", "2026-02-04", 0),
             ("manual:abc", "Delta", "BI Analyst", "2026-02-05", "Saved / Planning",
              "2026-02-05", 1)])
        conn.execute("INSERT INTO status_history (thread_id, from_status, to_status, note, "
                     "changed_at) VALUES ('b', NULL, NULL, 'recovered from noise filter', NULL)")
    Repository(settings).init()
    counts = build(settings)
    assert counts["fct_applications"] == 3          # noise and saved rows excluded
    assert not any(manifest(settings)["tests"].values())
