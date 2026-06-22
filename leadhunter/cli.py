"""
LeadHunter command-line interface.

Subcommands:
  scan       Run a one-off lead search and store results
  dashboard  Launch the Streamlit dashboard
  export     Export the entire DB to CSV / Excel
  report     Generate a daily/weekly/monthly report
  schedule   Run the scheduler in the foreground (for VPS / Windows Task)
  db-stats   Print high-level stats

Examples:
  python -m leadhunter scan --query "Dental Clinics" --city Asyut --country Egypt
  python -m leadhunter dashboard
  python -m leadhunter export --format csv
  python -m leadhunter report --period weekly
  python -m leadhunter schedule --preset daily
"""
from __future__ import annotations

import argparse
import os
import sys

from .config import get_config
from .core.database import Database
from .core.exporters import export_csv, export_excel
from .pipeline import Pipeline, SearchCriteria
from .reporting import generate_and_save
from .scheduler import PRESETS, get_scheduler, run_scheduled_scan
from .utils.logger import get_logger

log = get_logger(__name__)


import sys

def _print(s: str = "") -> None:
    try:
        print(s)
    except UnicodeEncodeError:
        # Fall back for terminals that don't support Unicode (e.g. cp1252)
        safe = s.encode("ascii", "replace").decode("ascii")
        print(safe)


def cmd_scan(args) -> int:
    crit = SearchCriteria(
        query=args.query, industry=args.industry or "", category=args.query,
        city=args.city or "", governorate=args.governorate or "",
        country=args.country or get_config().default_country,
        radius_km=args.radius or None, limit=args.limit,
        enrich=not args.no_enrich,
        sources=args.sources.split(",") if args.sources else
        ["openstreetmap", "search", "directories"])
    pipe = Pipeline()
    result = pipe.run(crit)
    _print(f"\n✅ {result.summary()}")
    _print(f"   Total leads in DB: {pipe.db.count()}")
    if args.export:
        path = export_csv(result.leads) if args.export == "csv" else export_excel(result.leads)
        _print(f"   Exported to: {path}")
    return 0


def cmd_dashboard(args) -> int:
    import subprocess
    cfg = get_config()
    app_path = os.path.join(os.path.dirname(__file__), "app.py")
    port = args.port or cfg.dashboard_port
    _print(f" launching dashboard on http://localhost:{port}")
    cmd = [sys.executable, "-m", "streamlit", "run", app_path,
           "--server.port", str(port), "--server.headless", "true"]
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        _print("dashboard stopped")
    return 0


def cmd_export(args) -> int:
    db = Database()
    leads = db.search(
        industry=args.industry or "", city=args.city or "",
        country=args.country or "", keyword=args.keyword or "",
        min_score=args.min_score or 0,
        limit=None if args.limit == 0 else args.limit)
    _print(f"Exporting {len(leads)} leads…")
    if args.format == "csv":
        path = export_csv(leads, filename=args.output)
    else:
        path = export_excel(leads, filename=args.output)
    _print(f"✅ Saved: {path}")
    return 0


def cmd_report(args) -> int:
    days = {"daily": 1, "weekly": 7, "monthly": 30}[args.period]
    label = args.period.capitalize()
    txt, jsn, report = generate_and_save(days=days, label=label)
    _print(f"✅ {label} report generated:")
    _print(f"   {txt}")
    _print(f"   {jsn}")
    _print("-" * 50)
    with open(txt, "r", encoding="utf-8") as f:
        _print(f.read())
    return 0


def cmd_schedule(args) -> int:
    if args.now:
        res = run_scheduled_scan(label="manual")
        _print(f"Scan complete: {res['totals']}")
        return 0
    sch = get_scheduler()
    for preset in args.preset:
        sch.schedule(preset)
    sch.start()
    _print(f"Scheduler running with: {args.preset}")
    _print("Press Ctrl+C to stop.")
    try:
        import time
        while True:
            time.sleep(60)
            info = sch.jobs_info()
            for j in info:
                _print(f"  next {j['preset']}: {j['next_run']}")
    except KeyboardInterrupt:
        _print("\nstopping scheduler…")
        sch.shutdown()
    return 0


def cmd_db_stats(args) -> int:
    db = Database()
    stats = db.stats()
    _print(f"Total leads    : {stats['total']}")
    _print(f"Avg score      : {stats['avg_score']}")
    _print(f"Industries     : {len(stats['by_industry'])}")
    _print(f"Cities         : {len(stats['by_city'])}")
    _print(f"Sources        : {len(stats['by_source_host'])}")
    _print("\nTop industries:")
    for k, v in list(stats["by_industry"].items())[:10]:
        _print(f"  {v:>4}  {k}")
    _print("\nTop cities:")
    for k, v in list(stats["by_city"].items())[:10]:
        _print(f"  {v:>4}  {k}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="leadhunter",
        description="LeadHunter — business lead generation platform")
    sub = p.add_subparsers(dest="command", required=True)

    # scan
    s = sub.add_parser("scan", help="Run a one-off lead search")
    s.add_argument("--query", "-q", required=True, help="Search keyword/phrase")
    s.add_argument("--industry", default="")
    s.add_argument("--city", default="")
    s.add_argument("--governorate", default="")
    s.add_argument("--country", default="")
    s.add_argument("--radius", type=int, default=0, help="Radius in km (0=default)")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--no-enrich", action="store_true", help="Skip website enrichment")
    s.add_argument("--sources", default="", help="Comma-separated: openstreetmap,search,directories")
    s.add_argument("--export", choices=["csv", "excel"], default=None)
    s.set_defaults(func=cmd_scan)

    # dashboard
    d = sub.add_parser("dashboard", help="Launch the Streamlit dashboard")
    d.add_argument("--port", type=int, default=None)
    d.set_defaults(func=cmd_dashboard)

    # export
    e = sub.add_parser("export", help="Export the DB to CSV/Excel")
    e.add_argument("--format", "-f", choices=["csv", "excel"], default="csv")
    e.add_argument("--output", "-o", default=None)
    e.add_argument("--industry", default="")
    e.add_argument("--city", default="")
    e.add_argument("--country", default="")
    e.add_argument("--keyword", default="")
    e.add_argument("--min-score", type=int, default=0)
    e.add_argument("--limit", type=int, default=0, help="0 = all")
    e.set_defaults(func=cmd_export)

    # report
    r = sub.add_parser("report", help="Generate a periodic report")
    r.add_argument("--period", choices=["daily", "weekly", "monthly"], default="daily")
    r.set_defaults(func=cmd_report)

    # schedule
    sc = sub.add_parser("schedule", help="Run scans on a schedule")
    sc.add_argument("--preset", nargs="+", default=["daily"],
                    choices=list(PRESETS.keys()))
    sc.add_argument("--now", action="store_true", help="Run all searches immediately then exit")
    sc.set_defaults(func=cmd_schedule)

    # db-stats
    ds = sub.add_parser("db-stats", help="Print database statistics")
    ds.set_defaults(func=cmd_db_stats)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        log.exception("CLI error: %s", e)
        _print(f"❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
