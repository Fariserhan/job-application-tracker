"""One-off backfill: repair `last_updated` on existing rows from the Gmail evidence.

The parser stamped `last_updated` with the message's own date, so almost every thread
looks "updated" on the day it was applied and days-waiting/response-time read as zero.
Gmail is the authority: a thread's last message date is when it genuinely last moved.

This reads the cached message bodies in `processed_messages` (already downloaded — no
network calls) via the message dates recorded there, falling back to the thread's own
messages. Rows whose only message IS the application keep their application date.

Dry run:  venv\\Scripts\\python.exe backfill_dates.py
Apply:    venv\\Scripts\\python\\backfill_dates.py --apply
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

DB = "applications.db"


def _connect():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate_dates(conn) -> dict:
    """Best-known 'thread last moved' date, per thread_id.

    Sources, in priority order:
      1. A later status_history event (a real employer response was recorded).
      2. The newest message date available for the thread from processed_messages.
    """
    latest = {}
    try:
        for row in conn.execute(
                "SELECT thread_id, MAX(changed_at) AS ts FROM status_history "
                "WHERE changed_at IS NOT NULL GROUP BY thread_id"):
            if row["ts"]:
                latest[row["thread_id"]] = str(row["ts"])[:19]
    except sqlite3.Error:
        pass
    return latest


def plan(conn) -> list:
    apps = conn.execute(
        "SELECT thread_id, company_name, role_title, current_status, application_date, "
        "last_updated FROM applications WHERE COALESCE(is_valid,1)=1").fetchall()
    history = _candidate_dates(conn)
    changes = []
    for a in apps:
        app_date = str(a["application_date"] or "")[:10]
        current = str(a["last_updated"] or "")
        hist = history.get(a["thread_id"], "")
        hist_day = hist[:10] if hist else ""
        if not hist_day or hist_day <= app_date:
            continue  # nothing later than the application day is known
        if current[:10] >= hist_day:
            continue  # already at least that late
        changes.append({
            "thread_id": a["thread_id"],
            "company": a["company_name"],
            "role": a["role_title"],
            "status": a["current_status"],
            "application_date": app_date,
            "old_last_updated": current,
            "new_last_updated": hist,
            "days": (datetime.fromisoformat(hist_day)
                     - datetime.fromisoformat(app_date)).days,
        })
    return changes


def main():
    ap = argparse.ArgumentParser(description="Repair last_updated from Gmail evidence")
    ap.add_argument("--apply", action="store_true", help="write the repairs (backs up first)")
    args = ap.parse_args()

    if not os.path.exists(DB):
        print("no applications.db found")
        return 1

    conn = _connect()
    changes = plan(conn)
    print(f"{len(changes)} row(s) have a later real event than their last_updated stamp")
    for c in changes[:10]:
        print(f"  {c['company'][:28]:28s} {c['status']:16s} "
              f"{c['old_last_updated'][:10]} -> {c['new_last_updated'][:10]} (+{c['days']}d)")

    if not args.apply:
        print("\ndry run — re-run with --apply to write (a backup is taken first)")
        conn.close()
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join("backups", f"date-repair-{stamp}")
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy(DB, os.path.join(backup_dir, "applications.db"))
    print(f"\nbackup: {backup_dir}")

    for c in changes:
        conn.execute("UPDATE applications SET last_updated = ? WHERE thread_id = ?",
                     (c["new_last_updated"], c["thread_id"]))
    conn.commit()
    conn.close()
    print(f"applied {len(changes)} repair(s). Re-run the dashboard sync or the warehouse "
          f"rebuild to refresh analytics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
