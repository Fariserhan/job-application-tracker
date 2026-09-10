import argparse
import sys
import time

import auth
import enricher
import gmail_fetcher
import matcher
import parser
import storage

# How far behind the sync watermark to re-query: if a metadata/body fetch failed on a
# previous run, the message's date could sit just under the watermark. Two days of overlap
# guarantees it is seen again (the body cache keeps the re-query nearly free).
SYNC_OVERLAP_MS = 2 * 24 * 60 * 60 * 1000

# Windows consoles default to a legacy codepage (cp1252) which crashes on emoji/• in job
# titles. Force UTF-8 with replacement so the CLI can never die on a print statement.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def run_fit_evaluation(notify=None, use_ai_fit: bool = False) -> dict:
    """Run the CV fit analyzer over every valid application that has JD text.

    With use_ai_fit=True (and AI available), scores are computed by the AI in batches and
    persisted with fit_source='ai' + the CV signature, so they are only recomputed when the
    CV changes. Deterministic scoring remains the offline fallback (and covers any AI
    failures). Never raises — a row that fails to score is skipped and counted, so a fit
    hiccup can't fail the whole sync.
    """
    import ai

    def _safe_analyze(r):
        try:
            return matcher.analyze(r["role_title"], r["job_description_snippet"] or "")
        except Exception:
            return None

    try:
        rows = storage.get_valid_rows_for_fit()
    except Exception:
        rows = []
    cv_sig = ai.cv_signature(matcher.get_cv_text())

    if use_ai_fit and ai.available():
        pending = [r for r in rows
                   if r.get("fit_source") != "ai" or (r.get("fit_cv_sig") or "") != cv_sig]
        ai_results = {}
        if pending:
            try:
                items = [{"id": r["thread_id"], "title": r["role_title"],
                          "description": r["job_description_snippet"] or ""} for r in pending]
                ai_results = ai.sanitize_fit_results(
                    ai.fit_score_batch(items, cv_text=matcher.get_cv_text(),
                                       model=ai.active_model()))
                storage.mark_fit_ai(ai_results, cv_sig=cv_sig)
            except Exception:
                ai_results = {}  # AI failed — deterministic fallback covers these rows
        # Only rows the AI was actually asked about may fall back to rules. Rows already
        # AI-scored against this CV are left untouched (never overwritten on a re-sync).
        fallback = {}
        for r in pending:
            if r["thread_id"] in ai_results:
                continue
            analysis = _safe_analyze(r)
            if analysis is not None:
                fallback[r["thread_id"]] = analysis
        if fallback:
            try:
                storage.save_fit_results(fallback, cv_sig=cv_sig)
            except Exception:
                pass
        scored = len(ai_results) + len(fallback)
        if notify:
            notify("fit", f"AI-scored {len(ai_results)} application(s) with CV fit "
                          f"({len(fallback)} fell back to rules) — AI scores are cached per CV "
                          f"version, so unchanged rows are never re-computed")
        return {"scored": scored}

    results = {}
    for row in rows:
        if (row.get("fit_source") == "rule" and (row.get("fit_cv_sig") or "") == cv_sig
                and row.get("fit_score") is not None):
            continue  # already scored against this exact CV
        analysis = _safe_analyze(row)
        if analysis is not None:
            results[row["thread_id"]] = analysis
    try:
        storage.save_fit_results(results, cv_sig=cv_sig)
    except Exception:
        pass
    if notify:
        notify("fit", f"Scored {len(results)} of {len(rows)} application(s) against CV profile "
                      f"({len(rows) - len(results)} already current)")
    return {"scored": len(results)}


def merge_thread_verdicts(stage1: list, verdicts: dict) -> dict:
    """Collapse per-message AI verdicts into one verdict per thread.

    `verdicts` is keyed by message id; `stage1` is ordered oldest → newest. Later messages
    win for company/role/status (filling blanks from earlier ones), while validity is OR-ed:
    a thread is an application if any of its messages says so.
    """
    merged = {}
    for meta, record in stage1:
        verdict = verdicts.get(meta["id"])
        if not isinstance(verdict, dict):
            continue
        thread_id = record["thread_id"]
        prev = merged.get(thread_id)
        if prev is None:
            merged[thread_id] = dict(verdict)
            continue
        out = dict(verdict)
        for key in ("company", "role", "status", "reason"):
            if not out.get(key) and prev.get(key):
                out[key] = prev[key]
        old_valid, new_valid = prev.get("is_valid"), verdict.get("is_valid")
        if old_valid is None:
            out["is_valid"] = new_valid
        elif new_valid is None:
            out["is_valid"] = old_valid
        else:
            out["is_valid"] = bool(old_valid) or bool(new_valid)
        merged[thread_id] = out
    return merged


def run(force_full: bool = False, notify=None, use_ai_fit: bool = False,
        use_ai_classify: bool = False) -> dict:
    """Full sync pipeline. `notify(stage, message)` drives live UI feedback.

    Stages: gmail -> metadata -> parse -> enrich -> fit -> done

    use_ai_fit: score applications with the AI (batched, persisted, never re-run).
    use_ai_classify: AI-classify emails with thread memory (valid? company/role/status),
                     recovering applications the deterministic filter may have missed.
    """
    import ai

    started = time.time()
    summary = {
        "found": 0, "inserted": 0, "updated": 0, "garbage": 0, "bodies": 0,
        "bodies_skipped": 0, "bodies_cached": 0, "bodies_refetched": 0, "enriched": 0,
        "enrich_failed": 0, "fit_scored": 0, "ai_classified": 0, "ghosted": 0,
        "elapsed": 0.0, "error": None, "csv_error": "", "truncated": False,
    }

    def emit(stage, message):
        if notify:
            try:
                notify(stage, message)
            except Exception:
                pass

    storage.init_db()

    emit("gmail", "Connecting to Gmail API...")
    creds = auth.get_credentials()
    service = gmail_fetcher.build_service(creds)
    profile = gmail_fetcher.get_profile(service)

    state = storage.load_state()
    last_epoch = None if force_full else state.get("last_sync_epoch")
    latest_epoch = last_epoch or 0

    # Re-query a small window behind the watermark: if a message's metadata/body fetch
    # failed transiently, its date could sit below the watermark and it would otherwise
    # never be seen again. The processed_messages body cache makes the overlap cheap.
    query_after = last_epoch
    if last_epoch:
        query_after = max(0, int(last_epoch) - SYNC_OVERLAP_MS)

    mode = "full re-sync" if force_full else ("incremental" if last_epoch else "full")
    message_ids = gmail_fetcher.search_messages(service, after_epoch=query_after)
    summary["found"] = len(message_ids)
    if len(message_ids) >= gmail_fetcher.MAX_SEARCH_RESULTS:
        summary["truncated"] = True
        emit("gmail", f"⚠️ Search hit the {gmail_fetcher.MAX_SEARCH_RESULTS}-message cap — "
                      f"raise GMAIL_MAX_RESULTS and re-run to see the rest.")
    emit("gmail", f"Querying Gmail API... {len(message_ids)} message(s) found ({mode} mode)")

    if message_ids:
        emit("metadata", f"Batch fetching metadata (50/batch) for {len(message_ids)} message(s)...")
        metas = gmail_fetcher.get_metadata_batch(service, message_ids)

        stage1 = []
        for meta in metas:
            latest_epoch = max(latest_epoch, meta["internal_date"])
            stage1.append((meta, parser.parse_message(meta)))
        # Process oldest → newest so the latest email always wins a thread's status.
        stage1.sort(key=lambda pair: pair[0]["internal_date"])

        body_ids = [meta["id"] for meta, record in stage1 if record["needs_body"]]
        fresh_body_ids = storage.filter_unprocessed(body_ids)
        fresh_set = set(fresh_body_ids)
        skipped_body_ids = [mid for mid in body_ids if mid not in fresh_set]

        # A Force Full Re-sync also re-evaluates metadata-only noise (garbage reasons can be
        # false negatives once the body is considered). Hard blacklist is subject-based and
        # deterministic, so those are not worth re-fetching.
        deep_ids = []
        if force_full:
            deep_ids = [
                meta["id"] for meta, record in stage1
                if (not record["needs_body"] and not record["is_valid"]
                    and not (record.get("filter_reason") or "").startswith(
                        ("blacklist", "blacklist-sender"))
                    and meta["id"] not in fresh_set)
            ]
        deep_set = set(deep_ids)
        cache_scan_ids = list(dict.fromkeys(skipped_body_ids + deep_ids))

        # Fresh bodies come from Gmail; already-processed bodies come from the local cache.
        fresh_bodies = (gmail_fetcher.get_bodies_batch(service, fresh_body_ids)
                        if fresh_body_ids else {})
        cached_bodies = (storage.get_processed_bodies(cache_scan_ids)
                         if cache_scan_ids else {})
        refetched_bodies = {}
        if force_full and cache_scan_ids:
            # Rows processed before the body cache existed have no stored text — fetch them
            # once so every future deep re-parse is offline. After this run the cache covers
            # the whole matching mailbox.
            need_refetch = [mid for mid in cache_scan_ids if mid not in cached_bodies]
            if need_refetch:
                emit("metadata", f"Deep re-parse: downloading {len(need_refetch)} body text(s) "
                                 f"to refresh the local cache…")
                refetched_bodies = gmail_fetcher.get_bodies_batch(service, need_refetch)

        bodies = {}
        bodies.update(cached_bodies)
        bodies.update(refetched_bodies)
        bodies.update(fresh_bodies)
        summary["bodies"] = len(fresh_bodies)
        summary["bodies_refetched"] = len(refetched_bodies)
        summary["bodies_cached"] = summary["bodies_cached"] + sum(
            1 for mid in cache_scan_ids if mid in cached_bodies)

        existing_map = storage.get_existing_map([m["thread_id"] for m, _ in stage1])

        emit("parse", "Filtering noise & parsing applications...")
        pending = []
        processed_pairs = []
        parsed_now = {}
        for meta, record in stage1:
            body = bodies.get(meta["id"])
            wants_body = record["needs_body"] or meta["id"] in deep_set
            if wants_body and body:
                # Only spend the optional LLM extraction on newly downloaded mail.
                record = parser.parse_message(meta, body_text=body,
                                              allow_llm=meta["id"] in fresh_set)
                if meta["id"] in fresh_set or meta["id"] in refetched_bodies:
                    processed_pairs.append((meta["id"], meta["thread_id"]))
                    parsed_now[meta["id"]] = body
            elif record["needs_body"]:
                # Body fetch failed (new message) — leave the row for the next sync.
                # Its metadata-only verdict cannot erase an existing valid row (OR merge).
                pass
            else:
                processed_pairs.append((meta["id"], meta["thread_id"]))

            existing = existing_map.get(record["thread_id"])
            already_enriched = existing and existing["job_description_snippet"]

            result = storage.upsert_application(record)
            if result in ("inserted", "updated"):
                summary[result] += 1
            if not record["is_valid"]:
                summary["garbage"] += 1
            elif result == "inserted" and record["job_url"] and not already_enriched:
                pending.append((record["thread_id"], record["job_url"]))

        storage.mark_messages_processed(processed_pairs, bodies=parsed_now)
        summary["bodies_skipped"] = len(skipped_body_ids)

        # AI email classification with thread memory (recovers false negatives, updates status)
        if use_ai_classify and ai.available():
            items = []
            for meta, record in stage1:
                existing = existing_map.get(record["thread_id"]) or {}
                # Skip messages already AI-classified unless this is a full re-sync
                if not force_full and existing.get("ai_classified"):
                    continue
                # Prefer the full sanitised body (fresh or cached) over the short snippet —
                # classification accuracy improves with the whole first message.
                body_excerpt = ""
                raw_body = bodies.get(meta["id"])
                if raw_body:
                    body_excerpt = parser.sanitize_body(raw_body)[:1200]
                items.append({
                    "id": meta["id"],
                    "thread": record["thread_id"],
                    "sender": meta.get("from", ""),
                    "subject": meta.get("subject", ""),
                    "snippet": body_excerpt or record.get("job_description_snippet")
                               or meta.get("snippet", ""),
                    "existing": {
                        "company_name": existing.get("company_name", ""),
                        "role_title": existing.get("role_title", ""),
                        "current_status": existing.get("current_status", ""),
                        "job_description_snippet": existing.get("job_description_snippet", ""),
                    },
                })
            if items:
                emit("parse", f"AI-classifying {len(items)} email(s) with thread memory...")
                try:
                    verdicts = ai.classify_batch(items, statuses=parser.STATUSES,
                                                 model=ai.active_model())
                    thread_verdicts = merge_thread_verdicts(stage1, verdicts)
                    n_cls = storage.apply_ai_classification(thread_verdicts)
                    summary["ai_classified"] = n_cls
                    if n_cls:
                        emit("parse", f"AI re-classified {n_cls} email(s) with thread memory — "
                                      f"recovered applications / advanced statuses included")
                    elif verdicts:
                        emit("parse", "AI classified emails but nothing needed changing")
                except Exception as exc:
                    emit("parse", f"AI classification skipped ({type(exc).__name__}) — "
                                  f"deterministic filter results kept")

        if pending:
            emit("enrich", f"Scraping job links concurrently ({len(pending)} URL(s), "
                           f"{enricher.TIMEOUT}s timeout, ThreadPoolExecutor)...")
            enriched = enricher.enrich_many(dict(pending))
            updated_links = storage.apply_enrichment(enriched)
            summary["enriched"] = updated_links
            summary["enrich_failed"] = sum(1 for p in enriched.values() if not p.get("ok"))

    fit = run_fit_evaluation(notify=emit, use_ai_fit=use_ai_fit)
    summary["fit_scored"] = fit["scored"]

    summary["ghosted"] = storage.apply_ghosting()
    try:
        storage.export_csv()
    except Exception as exc:
        # A locked CSV (e.g. open in Excel) must never fail a sync that already succeeded.
        summary["csv_error"] = f"{type(exc).__name__}: {exc}"
        emit("done", f"CSV export skipped ({type(exc).__name__}) — the database is safe; "
                     f"close the file and re-run --sync or use the dashboard export.")

    state["history_id"] = profile.get("historyId")
    state["last_sync_epoch"] = latest_epoch or None
    storage.save_state(state)

    summary["elapsed"] = time.time() - started
    emit("done", f"Done in {summary['elapsed']:.1f}s. "
                 f"Inserted {summary['inserted']}, updated {summary['updated']}, "
                 f"noise filtered {summary['garbage']}, enriched {summary['enriched']}, "
                 f"fit-scored {summary['fit_scored']}, auto-ghosted {summary['ghosted']}"
                 + (f" · deep re-parsed {summary['bodies_cached']} cached + "
                    f"{summary['bodies_refetched']} re-downloaded email(s)"
                    if (summary["bodies_cached"] or summary["bodies_refetched"]) else ""))
    return summary


def show():
    import os
    import sqlite3

    if not os.path.exists(storage.DB_PATH):
        print("No applications tracked yet. Run: python main.py --sync")
        return
    with sqlite3.connect(storage.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT company_name, role_title, source_platform, current_status, last_updated, "
            "is_valid, fit_score FROM applications ORDER BY last_updated DESC"
        ).fetchall()
    if not rows:
        print("No applications tracked yet. Run: python main.py --sync")
        return
    print(f"{'Company':<28} {'Role':<34} {'Platform':<12} {'Status':<16} {'Updated':<12} {'Fit':<6} Valid")
    print("-" * 124)
    for row in rows:
        fit = f"{row['fit_score']:.0f}%" if row["fit_score"] is not None else "-"
        print(
            f"{row['company_name']:<28} {row['role_title'][:32]:<34} "
            f"{row['source_platform']:<12} {row['current_status']:<16} {row['last_updated']:<12} "
            f"{fit:<6} {'yes' if row['is_valid'] else 'NO'}"
        )


def scan_market(notify=None) -> dict:
    """Live Malaysian job market discovery scan (used by the dashboard tab)."""
    import discovery

    if notify:
        notify("market", "Scanning LinkedIn, JobStreet, Hiredly & Remotive (16 workers)...")
    result = discovery.scan()
    storage.save_discovered(result["jobs"])
    if notify:
        notify("market", f"Found {len(result['jobs'])} unique live roles "
                         f"({result['source_counts']}) in {result['elapsed']:.1f}s")
    return result


def backup() -> str:
    """Snapshot DB + CSV + state + CV profile + AI cache into backups/<timestamp>/."""
    folder = storage.backup_all()
    print(f"[backup] wrote {folder}")
    return folder


def check() -> dict:
    """Data-quality report without any network calls."""
    import os
    import re
    import sqlite3

    if not os.path.exists(storage.DB_PATH):
        print("No database yet. Run: python main.py --sync")
        return {}
    _broken = re.compile(
        r"silverpop|url\.jobstreet\.com/ss|/ss/c/|e2ma\.net|seekcdn\.com|"
        r"\.(?:png|jpe?g|gif|webp|svg|ico)(?:[?#]|$)", re.IGNORECASE)

    def _is_broken(u):
        u = (u or "").strip()
        return bool(u) and u.lower() not in ("", "nan") and bool(_broken.search(u))

    with sqlite3.connect(storage.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        valid = conn.execute("SELECT COUNT(*) FROM applications WHERE is_valid=1").fetchone()[0]
        noise = total - valid
        statuses = dict(
            (r["current_status"], r["n"])
            for r in conn.execute(
                "SELECT current_status, COUNT(*) n FROM applications WHERE is_valid=1 GROUP BY 1")
        )
        rows = conn.execute(
            "SELECT company_name, role_title, job_description_snippet, job_url "
            "FROM applications WHERE is_valid=1"
        ).fetchall()
        no_jd = sum(1 for r in rows if len(r["job_description_snippet"] or "") < 240)
        broken = sum(1 for r in rows if _is_broken(r["job_url"]))
        platform_names = sorted({r["company_name"] for r in rows
                                 if parser.is_platform_company(r["company_name"])})
        dupes = {
            r["company_name"]: r["n"]
            for r in conn.execute(
                "SELECT company_name, COUNT(*) n FROM applications WHERE is_valid=1 "
                "GROUP BY company_name HAVING n > 1 ORDER BY n DESC")
        }
        avg_fit = conn.execute(
            "SELECT ROUND(AVG(fit_score),1) FROM applications WHERE is_valid=1 AND fit_score IS NOT NULL"
        ).fetchone()[0]
        scored = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE is_valid=1 AND fit_score IS NOT NULL"
        ).fetchone()[0]

    report = {
        "total": total, "valid": valid, "noise": noise, "statuses": statuses,
        "missing_jd": no_jd, "broken_links": broken, "scored_fit": scored, "avg_fit": avg_fit,
        "duplicate_companies": dupes, "platform_companies": platform_names,
    }
    print(f"Applications: {valid} valid / {total} total ({noise} noise)")
    print(f"Statuses: {statuses}")
    print(f"Fit scored: {scored} (avg {avg_fit}%)")
    print(f"Missing JD text: {no_jd}  ·  Broken/logo posting links: {broken}")
    print(f"Companies with >1 application: {dupes}")
    if platform_names:
        print(f"Platform placeholder companies: {platform_names}")
    if broken:
        print("Tip: in the dashboard, run 'Look up JDs & fix broken links' to repair these.")
    if platform_names:
        print("Tip: in the dashboard (🛠 Maintenance), run 'Fix platform company names' — "
              "then correct any leftovers in the row detail pop-up.")
    return report


if __name__ == "__main__":
    arg = argparse.ArgumentParser(description="Gmail job application tracker (Malaysia/regional)")
    arg.add_argument("--sync", action="store_true", help="Sync new emails and export tracker")
    arg.add_argument("--full", action="store_true", help="Force full re-sync (ignore incremental state)")
    arg.add_argument("--reset", action="store_true", help="Wipe applications.db, sync state and CSV, then full re-sync")
    arg.add_argument("--show", action="store_true", help="Print tracker summary")
    arg.add_argument("--market", action="store_true", help="Run live Malaysian job market discovery scan")
    arg.add_argument("--backup", action="store_true", help="Snapshot DB + CSV + state + CV + AI cache into backups/")
    arg.add_argument("--check", action="store_true", help="Print a data-quality report (no network)")
    args = arg.parse_args()

    if args.backup:
        backup()
    if args.check:
        check()
    if args.reset:
        storage.reset()
    if args.sync or args.full or args.reset:
        run(force_full=args.full or args.reset)
    elif args.market:
        result = scan_market()
        for job in result["jobs"][:15]:
            print(f"{job['fit_score']:5.1f}% | {job['source']:<10} | {job['title'][:45]:<45} | {job['company'][:25]} | {job['url']}")
    elif args.show:
        show()
    elif not (args.backup or args.check):
        arg.print_help()
        sys.exit(1)
