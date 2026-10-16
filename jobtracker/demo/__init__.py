"""Synthetic demo dataset, built through the real sync pipeline."""
from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

from ..config import Settings
from .mailbox import DemoMailbox

MANUAL_ENTRIES = [
    # (company, role, platform, days ago, status, note)
    ("Seroja Insurance Berhad", "Pricing Analyst", "Referral", 12, "Interview",
     "Referred by a former lecturer — panel interview booked."),
    ("Kasturi Analytics", "Data Analyst", "Company Site", 5, "Applied",
     "Applied on the careers page; no confirmation email."),
    ("Tanjung Re", "Graduate Actuarial Analyst", "Company Site", 40, "Rejected",
     "Rejected by phone after the case study."),
]


def build_demo(data_dir: Path | str, seed: int = 7, today: date | None = None) -> dict:
    """(Re)create a demo data directory: applications.db + warehouse.duckdb."""
    from ..store import Repository
    from ..sync import SyncEngine

    data_dir = Path(data_dir)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    settings = Settings(data_dir=data_dir.resolve(), gmail_after="2000/01/01",
                        enrich_enabled=False, llm_enabled=False).ensure_dirs()
    repo = Repository(settings)
    mailbox = DemoMailbox(seed=seed, today=today)
    summary = SyncEngine(settings, mailbox, repo, build_warehouse=False).run(full=True)

    day0 = today or date.today()
    for company, role, platform, ago, status, note in MANUAL_ENTRIES:
        tid = repo.add_manual_application(company, role, platform,
                                          applied_on=(day0 - timedelta(days=ago)).isoformat(),
                                          notes=note)
        if status != "Applied":
            repo.set_status(tid, status, note="logged manually",
                            occurred_on=(day0 - timedelta(days=max(ago - 6, 0))).isoformat())
    for row in repo.applications(include_noise=False)[:4]:
        repo.update_fields(row["thread_id"], notes="Follow up with the recruiter on LinkedIn.")

    from ..warehouse import build

    counts = build(settings)
    return {"messages": len(mailbox.messages), "sync": summary, "warehouse": counts,
            "data_dir": str(data_dir)}
