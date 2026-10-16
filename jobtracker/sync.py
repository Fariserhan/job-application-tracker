"""Sync engine: mailbox -> parse -> (LLM review) -> store -> enrich -> ghosting -> warehouse.

Stages are reported through ``progress(stage, message, fraction)`` so the CLI and the app
can render live feedback. A failure in an optional stage (enrichment, warehouse build)
never fails a sync whose data is already safely stored.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .config import Settings
from .gmail.client import MailSource, after_date
from .parsing.llm import LLMAssist
from .parsing.pipeline import ParsedMessage, parse_message
from .store import Repository

log = logging.getLogger(__name__)

Progress = Callable[[str, str, float], None]


@dataclass
class SyncSummary:
    mode: str = "incremental"
    found: int = 0
    new_messages: int = 0
    bodies_downloaded: int = 0
    bodies_from_cache: int = 0
    inserted: int = 0
    updated: int = 0
    noise: int = 0
    llm_reviewed: int = 0
    llm_cost_usd: float = 0.0
    enriched: int = 0
    ghosted: int = 0
    truncated: bool = False
    warehouse_rows: int = 0
    elapsed_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def headline(self) -> str:
        return (f"{self.found} emails scanned · {self.inserted} new · {self.updated} updated · "
                f"{self.noise} noise filtered · {self.ghosted} auto-ghosted "
                f"({self.elapsed_s:.1f}s)")


def _noop(stage: str, message: str, fraction: float) -> None:
    log.info("[%s] %s", stage, message)


class SyncEngine:
    def __init__(self, settings: Settings, source: MailSource, repo: Repository | None = None,
                 llm: LLMAssist | None = None, enricher: Callable[[dict], dict] | None = None,
                 build_warehouse: bool = True):
        self.settings = settings
        self.source = source
        self.repo = repo or Repository(settings)
        self.llm = llm or LLMAssist(settings, self.repo)
        self.enricher = enricher
        self.build_warehouse = build_warehouse

    def run(self, full: bool = False, progress: Progress = _noop) -> SyncSummary:
        started = time.time()
        summary = SyncSummary(mode="full re-parse" if full else "incremental")
        self.repo.init()
        state = self.repo.load_state()
        watermark = None if full else state.get("last_sync_epoch")
        if not watermark:
            summary.mode = "full re-parse" if full else "first sync"

        progress("search", "Searching Gmail for job mail…", 0.05)
        profile = self.source.profile()
        ids = self.source.search(after_date(self.settings.gmail_after, watermark),
                                 self.settings.gmail_max_results)
        summary.found = len(ids)
        if len(ids) >= self.settings.gmail_max_results:
            summary.truncated = True
            summary.warnings.append(f"Search hit the {self.settings.gmail_max_results}-message "
                                    "cap. Raise gmail_max_results to see older mail.")

        latest = int(watermark or 0)
        if ids:
            progress("metadata", f"Fetching headers for {len(ids)} emails…", 0.15)
            metas = sorted(self.source.metadata(ids), key=lambda m: m.get("internal_date") or 0)
            latest = max([latest, *[m.get("internal_date") or 0 for m in metas]])

            first_pass = [(meta, parse_message(meta)) for meta in metas]
            unseen = set(self.repo.filter_unprocessed([m["id"] for m in metas]))
            summary.new_messages = len(unseen)

            # Bodies: new mail that needs one, plus (on a full re-parse) every non-blacklisted
            # message so improved rules can re-judge old mail. They come from the local cache
            # when possible and are re-downloaded once otherwise.
            wanted = [meta["id"] for meta, rec in first_pass
                      if (meta["id"] in unseen and rec.needs_body)
                      or (full and not rec.filter_reason.startswith("blacklist"))]
            cached = self.repo.cached_bodies([m for m in wanted if m not in unseen])
            to_download = [m for m in wanted if m not in cached]
            progress("bodies", f"Downloading {len(to_download)} email bodies "
                               f"({len(cached)} from local cache)…", 0.35)
            downloaded = self.source.bodies(to_download) if to_download else {}
            bodies = {**cached, **downloaded}
            summary.bodies_downloaded = len(downloaded)
            summary.bodies_from_cache = len(cached)

            progress("parse", "Filtering noise and parsing applications…", 0.55)
            parsed: list[ParsedMessage] = []
            spend_before = self.repo.llm_spend()["cost_usd"]
            for meta, record in first_pass:
                body = bodies.get(meta["id"], "")
                if body:
                    record = parse_message(meta, body)
                elif record.needs_body and meta["id"] in unseen:
                    # Body fetch failed for brand-new mail: leave it for the next sync rather
                    # than storing a metadata-only verdict.
                    continue
                if record.ambiguous:
                    reviewed = self.llm.review(record, body, allow_new_call=meta["id"] in unseen
                                               or meta["id"] in downloaded)
                    if reviewed.parsed_by == "llm":
                        summary.llm_reviewed += 1
                    record = reviewed
                parsed.append(record)
                result = self.repo.apply_message(record)
                if result == "inserted":
                    summary.inserted += 1
                elif result == "updated":
                    summary.updated += 1
                if not record.is_valid:
                    summary.noise += 1
            self.repo.mark_processed(parsed, downloaded)
            summary.llm_cost_usd = round(self.repo.llm_spend()["cost_usd"] - spend_before, 5)
            if self.llm.disabled_reason and self.settings.llm_enabled:
                summary.warnings.append(f"Claude fallback: {self.llm.disabled_reason}")

        if self.settings.enrich_enabled and self.enricher is not None:
            pending = self.repo.threads_needing_enrichment()
            if pending:
                progress("enrich", f"Reading {len(pending)} job postings…", 0.75)
                try:
                    summary.enriched = self.repo.apply_enrichment(self.enricher(pending))
                except Exception as exc:
                    summary.warnings.append(f"Enrichment skipped ({type(exc).__name__})")

        summary.ghosted = self.repo.apply_ghosting(self.settings.ghost_after_days)
        state.update({"last_sync_epoch": latest or None,
                      "history_id": profile.get("historyId"),
                      "last_sync_at": datetime.now().isoformat(timespec="seconds")})
        self.repo.save_state(state)

        if self.build_warehouse:
            progress("warehouse", "Rebuilding the analytics warehouse…", 0.9)
            try:
                from .warehouse import build

                summary.warehouse_rows = build(self.settings).get("fct_applications", 0)
            except Exception as exc:
                summary.warnings.append(f"Warehouse build failed: {type(exc).__name__}: {exc}")

        summary.elapsed_s = round(time.time() - started, 2)
        progress("done", summary.headline(), 1.0)
        return summary


def reparse_from_cache(settings: Settings) -> int:
    """Re-run the current rules over every cached body (offline). Returns rows touched."""
    from .parsing.pipeline import parse_message as _parse

    repo = Repository(settings)
    repo.init()
    touched = 0
    with repo.connect() as conn:
        rows = conn.execute(
            "SELECT message_id, thread_id, body_text, message_date, subject, sender "
            "FROM processed_messages ORDER BY message_date").fetchall()
    for row in rows:
        if not row["body_text"] or not row["subject"]:
            continue  # v1 rows cached no subject; a Gmail full re-parse refreshes them
        meta = {"id": row["message_id"], "thread_id": row["thread_id"],
                "subject": row["subject"], "from": row["sender"], "snippet": "",
                "internal_date": 0, "date_header": ""}
        record = _parse(meta, row["body_text"] or "")
        record.message_date = row["message_date"] or record.message_date
        if repo.apply_message(record) == "updated":
            touched += 1
    return touched
