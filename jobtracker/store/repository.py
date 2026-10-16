"""Operational store: every write the tracker makes goes through ``Repository``.

Merge rules (one thread = one application, fed by many emails):

* **validity is an OR across the thread**: one confirming email proves the application;
  a later marketing-looking email can never erase it. Manual verdicts are sticky.
* **status only moves on newer evidence**: an email older than the thread's latest status
  event never rolls it back, and a generic "your application…" email never demotes an
  interview to "Applied". Every change is written to ``status_events`` dated by the email.
* **identity only improves**: a platform placeholder ("JobStreet", "Workday") never
  replaces a real employer; a junk fragment never replaces a real role title.
* **dates mean something**: ``application_date`` is the earliest message; ``last_updated``
  advances only when a message actually changes the record, so "days waiting" is real.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import domain
from ..config import Settings
from ..parsing.categorize import (
    classify_industry,
    classify_job_type,
    classify_role_family,
    classify_seniority,
)
from ..parsing.extract import is_platform_company, looks_like_role
from ..parsing.pipeline import ParsedMessage
from .schema import ensure_schema, needs_migration, now

BODY_CACHE_CHARS = 8000
KEEP_BACKUPS = 15


def _today() -> str:
    return date.today().isoformat()


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = Path(settings.db_path)

    # ------------------------------------------------------------------ connection
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> int:
        """Create or upgrade the schema; backs the database up before any migration."""
        self.settings.ensure_dirs()
        if self.db_path.exists():
            with self.connect() as conn:
                pending = needs_migration(conn)
            if pending:
                self.backup("pre-v2-migration")
        with self.connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error:
                pass  # WAL is best-effort (unsupported on some network filesystems)
            return ensure_schema(conn)

    # ------------------------------------------------------------------ events
    @staticmethod
    def _log_event(conn, thread_id: str, from_status: str | None, to_status: str,
                   occurred_on: str, source: str, message_id: str = "", note: str = "") -> None:
        conn.execute(
            "INSERT INTO status_events (thread_id, from_status, to_status, occurred_on, "
            "recorded_at, source, message_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, from_status, to_status, occurred_on[:10] or _today(), now(), source,
             message_id or "", note or ""))

    @staticmethod
    def _latest_event_day(conn, thread_id: str) -> str:
        row = conn.execute("SELECT MAX(occurred_on) FROM status_events WHERE thread_id=?",
                           (thread_id,)).fetchone()
        return (row[0] or "") if row else ""

    # ------------------------------------------------------------------ gmail merge
    def apply_message(self, msg: ParsedMessage) -> str:
        """Merge one parsed email into its thread. Returns inserted | updated | unchanged."""
        day = msg.message_date or _today()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM applications WHERE thread_id=?",
                                    (msg.thread_id,)).fetchone()
            if existing is None:
                return self._insert_from_message(conn, msg, day)
            return self._merge_message(conn, existing, msg, day)

    def _insert_from_message(self, conn, msg: ParsedMessage, day: str) -> str:
        status = msg.status
        stamp = now()
        conn.execute(
            """INSERT INTO applications (thread_id, company_name, role_title, source_platform,
                   application_date, current_status, last_updated, job_description_snippet,
                   job_url, gmail_link, latest_subject, is_valid, filter_reason, job_type,
                   seniority_level, industry, role_family, parse_confidence, parsed_by,
                   origin, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'gmail', ?, ?)""",
            (msg.thread_id, msg.company_name, msg.role_title, msg.source_platform, day, status,
             day, msg.body_excerpt, msg.job_url, msg.gmail_link, msg.subject,
             1 if msg.is_valid else 0, msg.filter_reason, msg.job_type, msg.seniority_level,
             msg.industry, msg.role_family, msg.confidence, msg.parsed_by, stamp, stamp))
        if msg.is_valid:
            self._log_event(conn, msg.thread_id, None, domain.APPLIED, day, "gmail",
                            msg.message_id, "first email in thread")
            if status != domain.APPLIED:
                self._log_event(conn, msg.thread_id, domain.APPLIED, status, day, msg.parsed_by
                                if msg.parsed_by == "llm" else "gmail", msg.message_id,
                                msg.subject[:160])
        return "inserted"

    def _merge_message(self, conn, existing: sqlite3.Row, msg: ParsedMessage, day: str) -> str:
        current = domain.normalise_status(existing["current_status"])

        # --- validity: OR across the thread; manual verdicts are sticky ---------------
        if (existing["filter_reason"] or "").startswith("manual"):
            valid, reason = existing["is_valid"], existing["filter_reason"]
        else:
            valid = 1 if (existing["is_valid"] == 1 or msg.is_valid) else 0
            if msg.is_valid:
                reason = msg.filter_reason or existing["filter_reason"] or ""
            elif existing["is_valid"] == 1:
                reason = existing["filter_reason"] or ""
            else:
                reason = msg.filter_reason or existing["filter_reason"] or "no positive affirmation"
        became_valid = valid == 1 and existing["is_valid"] != 1

        # --- identity only improves -------------------------------------------------
        company = existing["company_name"]
        if msg.company_name and (is_platform_company(company) or company == "Unknown") \
                and not is_platform_company(msg.company_name):
            company = msg.company_name
        role = existing["role_title"] or ""
        if msg.role_title and (not looks_like_role(role)):
            role = msg.role_title
        platform = existing["source_platform"] or "Other"
        if platform == "Other" and msg.source_platform not in ("", "Other"):
            platform = msg.source_platform

        # --- status: newer evidence only, never a generic demotion -------------------
        new_status = current
        detected = msg.detected_status
        latest = self._latest_event_day(conn, msg.thread_id)
        if msg.is_valid and detected and detected != current and day >= (latest or ""):
            demotion = detected == domain.APPLIED and current not in (domain.GHOSTED, domain.SAVED)
            if not demotion:
                new_status = detected
        elif msg.is_valid and current == domain.GHOSTED and day > (latest or ""):
            # Any new employer email revives a thread the ghosting rule had closed.
            new_status = detected or domain.APPLIED

        app_date = min(existing["application_date"] or day, day) if msg.is_valid else \
            existing["application_date"]

        changed = (
            new_status != current or company != existing["company_name"]
            or role != (existing["role_title"] or "") or platform != existing["source_platform"]
            or bool(msg.job_url and not existing["job_url"])
            or bool(msg.body_excerpt and not existing["job_description_snippet"])
            or valid != existing["is_valid"]
        )
        last_updated = existing["last_updated"]
        if changed and msg.is_valid:
            last_updated = max(str(existing["last_updated"] or ""), day)

        if became_valid and not conn.execute(
                "SELECT 1 FROM status_events WHERE thread_id=? LIMIT 1",
                (msg.thread_id,)).fetchone():
            self._log_event(conn, msg.thread_id, None, domain.APPLIED, app_date, "gmail",
                            msg.message_id, "thread confirmed as an application")
        if new_status != current:
            self._log_event(conn, msg.thread_id, current, new_status, day,
                            "llm" if msg.parsed_by == "llm" else "gmail", msg.message_id,
                            msg.subject[:160])

        conn.execute(
            """UPDATE applications SET company_name=?, role_title=?, source_platform=?,
                   current_status=?, application_date=?, last_updated=?, latest_subject=?,
                   is_valid=?, filter_reason=?,
                   job_type = CASE WHEN ? <> '' THEN ? ELSE job_type END,
                   seniority_level = CASE WHEN ? <> '' THEN ? ELSE seniority_level END,
                   industry = CASE WHEN ? <> '' THEN ? ELSE industry END,
                   role_family = ?,
                   job_url = CASE WHEN COALESCE(job_url, '') = '' THEN ? ELSE job_url END,
                   job_description_snippet = CASE WHEN COALESCE(job_description_snippet, '') = ''
                       THEN ? ELSE job_description_snippet END,
                   gmail_link = CASE WHEN COALESCE(gmail_link, '') = '' THEN ? ELSE gmail_link END,
                   parse_confidence = MAX(COALESCE(parse_confidence, 0), ?),
                   updated_at=?
               WHERE thread_id=?""",
            (company, role, platform, new_status, app_date, last_updated, msg.subject, valid,
             reason, msg.job_type, msg.job_type, msg.seniority_level, msg.seniority_level,
             msg.industry, msg.industry, classify_role_family(role), msg.job_url,
             msg.body_excerpt, msg.gmail_link, msg.confidence, now(), msg.thread_id))
        return "updated" if changed else "unchanged"

    # ------------------------------------------------------------------ message cache
    def filter_unprocessed(self, message_ids: list[str]) -> list[str]:
        if not message_ids:
            return []
        known: set[str] = set()
        with self.connect() as conn:
            for chunk in _chunks(message_ids, 500):
                marks = ",".join("?" * len(chunk))
                known.update(r[0] for r in conn.execute(
                    f"SELECT message_id FROM processed_messages WHERE message_id IN ({marks})",
                    chunk))
        return [m for m in message_ids if m not in known]

    def cached_bodies(self, message_ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        with self.connect() as conn:
            for chunk in _chunks(message_ids, 500):
                marks = ",".join("?" * len(chunk))
                out.update({r[0]: r[1] for r in conn.execute(
                    f"SELECT message_id, body_text FROM processed_messages "
                    f"WHERE message_id IN ({marks}) AND COALESCE(body_text, '') <> ''", chunk)})
        return out

    def mark_processed(self, messages: Iterable[ParsedMessage], bodies: dict[str, str]) -> int:
        rows = [(m.message_id, m.thread_id, now(), (bodies.get(m.message_id) or "")[:BODY_CACHE_CHARS],
                 m.message_date, m.subject[:300], m.sender[:200], 1 if m.is_valid else 0,
                 m.filter_reason, m.detected_status, m.confidence, m.parsed_by)
                for m in messages if m.message_id]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO processed_messages (message_id, thread_id, processed_at, body_text,
                       message_date, subject, sender, is_valid, filter_reason, detected_status,
                       confidence, parsed_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(message_id) DO UPDATE SET
                       thread_id=excluded.thread_id,
                       body_text=CASE WHEN excluded.body_text <> '' THEN excluded.body_text
                                      ELSE processed_messages.body_text END,
                       message_date=CASE WHEN excluded.message_date <> '' THEN excluded.message_date
                                         ELSE processed_messages.message_date END,
                       subject=excluded.subject, sender=excluded.sender,
                       is_valid=excluded.is_valid, filter_reason=excluded.filter_reason,
                       detected_status=excluded.detected_status, confidence=excluded.confidence,
                       parsed_by=excluded.parsed_by""", rows)
        return len(rows)

    # ------------------------------------------------------------------ enrichment
    def threads_needing_enrichment(self, limit: int = 60) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT thread_id, job_url FROM applications WHERE is_valid=1 AND job_url <> '' "
                "AND LENGTH(COALESCE(job_description_snippet, '')) < 240 "
                "AND COALESCE(location, '') = '' ORDER BY application_date DESC LIMIT ?",
                (limit,)).fetchall()
        return {r[0]: r[1] for r in rows}

    def apply_enrichment(self, results: dict[str, dict]) -> int:
        updated = 0
        with self.connect() as conn:
            for thread_id, payload in results.items():
                if not payload.get("ok"):
                    continue
                title = (payload.get("title") or "").strip()
                desc = (payload.get("description") or "").strip()
                text = f"{title} — {desc}" if title and desc else (title or desc)
                conn.execute(
                    """UPDATE applications SET
                           job_description_snippet = CASE WHEN ? <> '' AND
                               LENGTH(?) > LENGTH(COALESCE(job_description_snippet, ''))
                               THEN ? ELSE job_description_snippet END,
                           location = COALESCE(NULLIF(?, ''), location),
                           salary_text = COALESCE(NULLIF(?, ''), salary_text),
                           employment_type = COALESCE(NULLIF(?, ''), employment_type),
                           posted_date = COALESCE(NULLIF(?, ''), posted_date),
                           updated_at = ?
                       WHERE thread_id = ?""",
                    (text, text, text[:4000], payload.get("location", ""),
                     payload.get("salary", ""), payload.get("employment_type", ""),
                     payload.get("posted_date", ""), now(), thread_id))
                updated += 1
        return updated

    # ------------------------------------------------------------------ rules
    def apply_ghosting(self, after_days: int, today: str | None = None) -> int:
        """Applied rows silent for more than ``after_days`` become Ghosted.

        The event is dated the day the threshold was crossed, not the day the sync ran.
        """
        today = today or _today()
        cutoff = (date.fromisoformat(today) - timedelta(days=after_days)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT thread_id, last_updated FROM applications WHERE is_valid=1 "
                "AND current_status='Applied' AND substr(last_updated, 1, 10) < ?",
                (cutoff,)).fetchall()
            for row in rows:
                crossed = (date.fromisoformat(row["last_updated"][:10])
                           + timedelta(days=after_days)).isoformat()
                conn.execute("UPDATE applications SET current_status='Ghosted', updated_at=? "
                             "WHERE thread_id=?", (now(), row["thread_id"]))
                self._log_event(conn, row["thread_id"], domain.APPLIED, domain.GHOSTED,
                                min(crossed, today), "system",
                                note=f"no reply for {after_days}+ days")
        return len(rows)

    # ------------------------------------------------------------------ manual edits
    def set_status(self, thread_id: str, status: str, note: str = "", source: str = "manual",
                   occurred_on: str | None = None) -> bool:
        status = domain.normalise_status(status)
        day = (occurred_on or _today())[:10]
        with self.connect() as conn:
            row = conn.execute("SELECT current_status, application_date FROM applications "
                               "WHERE thread_id=?", (thread_id,)).fetchone()
            if not row:
                return False
            if row["current_status"] == status:
                return True
            sets = "current_status=?, last_updated=?, updated_at=?"
            params: list = [status, day, now()]
            if row["current_status"] == domain.SAVED and status == domain.APPLIED:
                sets += ", application_date=?"
                params.append(day)
            conn.execute(f"UPDATE applications SET {sets} WHERE thread_id=?", [*params, thread_id])
            self._log_event(conn, thread_id, row["current_status"], status, day, source,
                            note=note)
        return True

    def update_fields(self, thread_id: str, **fields) -> bool:
        allowed = {"company_name", "role_title", "source_platform", "job_url", "notes",
                   "location", "salary_text", "job_type", "industry", "seniority_level"}
        sets = {k: (str(v or "").strip()[:2000]) for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        if "role_title" in sets:
            sets["role_family"] = classify_role_family(sets["role_title"])
        sets["updated_at"] = now()
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE applications SET {', '.join(f'{k}=?' for k in sets)} WHERE thread_id=?",
                [*sets.values(), thread_id])
            return cur.rowcount > 0

    def set_valid(self, thread_id: str, is_valid: bool, reason: str = "") -> bool:
        """Manual noise verdict. Sticky in both directions until changed again."""
        reason = (reason or "").strip() or ("manual recovery" if is_valid else "manual: marked as noise")
        if not reason.startswith("manual"):
            reason = f"manual: {reason}"
        with self.connect() as conn:
            row = conn.execute("SELECT is_valid, application_date FROM applications WHERE thread_id=?",
                               (thread_id,)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE applications SET is_valid=?, filter_reason=?, updated_at=? "
                         "WHERE thread_id=?", (1 if is_valid else 0, reason, now(), thread_id))
            if is_valid and not conn.execute("SELECT 1 FROM status_events WHERE thread_id=? LIMIT 1",
                                             (thread_id,)).fetchone():
                self._log_event(conn, thread_id, None, domain.APPLIED, row["application_date"],
                                "manual", note="recovered from the noise filter")
        return True

    def add_manual_application(self, company: str, role: str, platform: str = "Direct ATS",
                               applied_on: str | None = None, job_url: str = "",
                               notes: str = "", status: str = domain.APPLIED) -> str:
        """Log an application that never produced an email (careers-page apply, referral)."""
        thread_id = f"manual:{uuid.uuid4().hex[:12]}"
        day = (applied_on or _today())[:10]
        status = domain.normalise_status(status)
        role = role.strip()
        stamp = now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO applications (thread_id, company_name, role_title, source_platform,
                       application_date, current_status, last_updated, job_url, latest_subject,
                       is_valid, filter_reason, job_type, seniority_level, industry, role_family,
                       notes, parsed_by, origin, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'manual entry', ?, ?, ?, ?, ?, 'manual',
                           'manual', ?, ?)""",
                (thread_id, company.strip() or "Unknown", role, platform or "Other", day, status,
                 day, job_url.strip(), f"Manual entry: {role}", classify_job_type(role),
                 classify_seniority(role), classify_industry(f"{company} {role}"),
                 classify_role_family(role), notes.strip(), stamp, stamp))
            if status != domain.SAVED:
                self._log_event(conn, thread_id, None, domain.APPLIED, day, "manual",
                                note="logged manually")
                if status != domain.APPLIED:
                    self._log_event(conn, thread_id, domain.APPLIED, status, day, "manual")
        return thread_id

    def record_follow_up(self, thread_id: str, note: str = "") -> bool:
        """Log that the candidate chased this application: a dated line is appended to the
        notes and the silence clock restarts (``last_updated`` = today), so the follow-up
        queue and the ghosting rule measure silence since the nudge, not since applying."""
        day = _today()
        line = f"[{day}] Followed up" + (f": {note.strip()}" if note.strip() else "")
        with self.connect() as conn:
            row = conn.execute("SELECT notes FROM applications WHERE thread_id=?",
                               (thread_id,)).fetchone()
            if not row:
                return False
            notes = "\n".join(filter(None, [(row["notes"] or "").strip(), line]))
            conn.execute("UPDATE applications SET notes=?, last_updated=?, updated_at=? "
                         "WHERE thread_id=?", (notes[-4000:], day, now(), thread_id))
        return True

    def delete_application(self, thread_id: str) -> bool:
        with self.connect() as conn:
            conn.execute("DELETE FROM status_events WHERE thread_id=?", (thread_id,))
            return conn.execute("DELETE FROM applications WHERE thread_id=?",
                                (thread_id,)).rowcount > 0

    # ------------------------------------------------------------------ reads
    def applications(self, include_noise: bool = True) -> list[dict]:
        where = "" if include_noise else "WHERE is_valid = 1"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM applications {where} ORDER BY application_date DESC")]

    def get(self, thread_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM applications WHERE thread_id=?",
                               (thread_id,)).fetchone()
        return dict(row) if row else None

    def events(self, thread_id: str | None = None, limit: int = 500) -> list[dict]:
        with self.connect() as conn:
            if thread_id:
                rows = conn.execute(
                    "SELECT * FROM status_events WHERE thread_id=? ORDER BY occurred_on, event_id",
                    (thread_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT e.*, a.company_name, a.role_title FROM status_events e "
                    "JOIN applications a USING (thread_id) WHERE a.is_valid = 1 "
                    "ORDER BY e.occurred_on DESC, e.event_id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, SUM(is_valid) AS valid FROM applications").fetchone()
            msgs = conn.execute("SELECT COUNT(*) FROM processed_messages").fetchone()[0]
        total = int(row["total"] or 0)
        valid = int(row["valid"] or 0)
        return {"total": total, "valid": valid, "noise": total - valid, "messages": int(msgs)}

    def table(self, name: str) -> list[dict]:
        if name not in ("applications", "status_events", "processed_messages", "llm_calls"):
            raise ValueError(name)
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {name}")]

    # ------------------------------------------------------------------ LLM ledger
    def llm_cached(self, message_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM llm_calls WHERE message_id=?",
                               (message_id,)).fetchone()
        return dict(row) if row else None

    def record_llm_call(self, message_id: str, model: str, input_tokens: int,
                        output_tokens: int, cost_usd: float, result: dict | None,
                        error: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_calls (message_id, model, created_at, input_tokens, "
                "output_tokens, cost_usd, result_json, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, model, now(), int(input_tokens), int(output_tokens),
                 float(cost_usd), json.dumps(result) if result else "", error[:500]))

    def llm_spend(self, month: str | None = None) -> dict:
        month = month or datetime.now().strftime("%Y-%m")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost, "
                "COALESCE(SUM(input_tokens), 0) AS tin, COALESCE(SUM(output_tokens), 0) AS tout "
                "FROM llm_calls WHERE substr(created_at, 1, 7) = ?", (month,)).fetchone()
        return {"month": month, "calls": int(row["calls"]), "cost_usd": float(row["cost"]),
                "input_tokens": int(row["tin"]), "output_tokens": int(row["tout"])}

    # ------------------------------------------------------------------ sync state
    def load_state(self) -> dict:
        default = {"last_sync_epoch": None, "history_id": None, "last_sync_at": None}
        path = self.settings.state_path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
        return {**default, **data} if isinstance(data, dict) else default

    def save_state(self, state: dict) -> None:
        path = self.settings.state_path
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------ backups
    def backup(self, label: str = "manual") -> Path:
        """Consistent snapshot via the SQLite backup API (WAL-safe). Keeps the newest 15."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = self.settings.backups_dir / f"{stamp}-{label}"
        folder.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            src = sqlite3.connect(self.db_path)
            dst = sqlite3.connect(folder / "applications.db")
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        if self.settings.state_path.exists():
            shutil.copy2(self.settings.state_path, folder / self.settings.state_path.name)
        existing = sorted(p for p in self.settings.backups_dir.iterdir() if p.is_dir())
        for old in existing[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)
        return folder


def _chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
