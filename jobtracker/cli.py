"""Command line: ``python -m jobtracker <command>`` (or ``jobtracker <command>`` once installed).

    sync [--full]       pull Gmail, parse, store, enrich, rebuild the warehouse
    build               rebuild the DuckDB warehouse from applications.db (+ data tests)
    demo [--dir PATH]   create the synthetic demo dataset (never touches your data)
    show                print the tracker as a table
    check               data-quality report (offline)
    repair [--apply]    re-judge stored rows with the current rules (backs up first)
    reparse             re-run the current parser over the cached email bodies (offline)
    export              write the Excel workbook + Power BI / Tableau pack
    report [--out F]    write the findings report (Markdown) from the warehouse
    eval                score the parser against the labelled email corpus
    backup              snapshot the database
    app                 launch the Streamlit app
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import load_settings


def _print_progress(stage: str, message: str, fraction: float) -> None:
    print(f"[{fraction:>4.0%}] {stage:<9} {message}", flush=True)


def cmd_sync(args, settings) -> int:
    from .enrich import enrich_many
    from .gmail import GmailClient
    from .sync import SyncEngine

    source = GmailClient.from_settings(settings)
    summary = SyncEngine(settings, source, enricher=enrich_many).run(
        full=args.full, progress=_print_progress)
    for warning in summary.warnings:
        print(f"warning: {warning}")
    return 0


def cmd_build(args, settings) -> int:
    from .warehouse import build

    counts = build(settings)
    print(f"warehouse: {counts['fct_applications']} applications, "
          f"{counts['fct_status_events']} events, {counts['tests_failed']} failing data tests "
          f"({counts['seconds']}s)")
    return 1 if counts["tests_failed"] else 0


def cmd_demo(args, settings) -> int:
    from pathlib import Path

    from .config import PROJECT_ROOT
    from .demo import build_demo

    target = Path(args.dir) if args.dir else PROJECT_ROOT / "demo_data"
    result = build_demo(target, seed=args.seed)
    print(f"demo: {result['messages']} synthetic emails -> "
          f"{result['warehouse']['fct_applications']} applications in {target}")
    print(f"run it:  JOBTRACKER_DATA_DIR={target} streamlit run app/streamlit_app.py")
    return 0


def cmd_show(args, settings) -> int:
    from .store import Repository

    rows = Repository(settings).applications(include_noise=False)
    if not rows:
        print("No applications yet — run: python -m jobtracker sync")
        return 0
    print(f"{'Company':<28} {'Role':<34} {'Platform':<12} {'Status':<16} {'Applied':<10}")
    print("-" * 104)
    for r in rows:
        print(f"{r['company_name'][:27]:<28} {(r['role_title'] or '—')[:33]:<34} "
              f"{r['source_platform'][:11]:<12} {r['current_status']:<16} {r['application_date'][:10]}")
    return 0


def cmd_check(args, settings) -> int:
    from .warehouse import connect
    from .warehouse.metrics import Metrics

    with connect(settings) as con:
        frame = Metrics(con, settings).query("data_quality")
    for row in frame.itertuples():
        pct = 100.0 * row.passing / row.total if row.total else 100.0
        print(f"{row.check_name:<34} {row.passing:>5}/{row.total:<5} {pct:5.1f}%")
    return 0


def cmd_repair(args, settings) -> int:
    from .repair import run

    plan, counts = run(settings, apply=args.apply)
    print(f"{len(plan)} row(s) {'repaired' if args.apply else 'would change (dry run)'}: {counts}")
    return 0


def cmd_reparse(args, settings) -> int:
    from .sync import reparse_from_cache
    from .warehouse import build

    touched = reparse_from_cache(settings)
    build(settings)
    print(f"re-parsed cached mail: {touched} application(s) updated")
    return 0


def cmd_export(args, settings) -> int:
    from .bi import export_all

    written = export_all(settings)
    for name, path in written.items():
        print(f"{name:<24} {path}")
    return 0


def cmd_report(args, settings) -> int:
    from pathlib import Path

    from .report import write_report

    path = write_report(settings, Path(args.out) if args.out else None, demo=args.demo)
    print(f"report written: {path}")
    return 0


def cmd_eval(args, settings) -> int:
    from .parsing.evaluate import main as evaluate

    return evaluate(write_report=args.write)


def cmd_backup(args, settings) -> int:
    from .store import Repository

    print(Repository(settings).backup("manual"))
    return 0


def cmd_app(args, settings) -> int:
    import subprocess

    from .config import PROJECT_ROOT

    return subprocess.call([sys.executable, "-m", "streamlit", "run",
                            str(PROJECT_ROOT / "app" / "streamlit_app.py")])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobtracker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", help="override the data directory")
    parser.add_argument("-v", "--verbose", action="store_true")
    # The same flags are accepted after the subcommand too ("jobtracker export --data-dir X");
    # SUPPRESS keeps a subcommand from overwriting a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS,
                        help="override the data directory")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common])

    p = command("sync")
    p.add_argument("--full", action="store_true", help="re-parse the whole mailbox")
    command("build")
    p = command("demo")
    p.add_argument("--dir")
    p.add_argument("--seed", type=int, default=7)
    command("show")
    command("check")
    p = command("repair")
    p.add_argument("--apply", action="store_true")
    command("reparse")
    command("export")
    p = command("report")
    p.add_argument("--out")
    p.add_argument("--demo", action="store_true", help="label the report as synthetic data")
    p = command("eval")
    p.add_argument("--write", action="store_true", help="write docs/parser-evaluation.md")
    command("backup")
    command("app")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: never die on an emoji
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    settings = load_settings(args.data_dir)
    handler = globals()[f"cmd_{args.command}"]
    return int(handler(args, settings) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
