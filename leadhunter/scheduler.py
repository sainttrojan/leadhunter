"""
Scan scheduling for unattended operation on a Windows VPS.

Backed by APScheduler (BackgroundScheduler) so it runs in-process alongside
the Streamlit dashboard. The same job registry is also surfaced in the
dashboard so users can see when the next scan is due.

Schedule presets:
  * daily   — every day at 02:00
  * weekly  — every Monday at 03:00
  * monthly — first day of each month at 04:00

Each scan runs the Pipeline for every search stored in `searches.json`,
then writes a daily report. Failures are logged but never crash the
scheduler — the next tick simply retries.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_config
from .core.database import Database
from .pipeline import Pipeline, SearchCriteria
from .reporting import generate_and_save
from .utils.logger import get_logger

log = get_logger(__name__)

# Where the user-defined recurring searches live
SEARCHES_FILE = "searches.json"


# ---------------------------------------------------------------------------
# Recurring searches registry
# ---------------------------------------------------------------------------
def searches_path() -> str:
    cfg = get_config()
    return os.path.join(cfg.base_dir, SEARCHES_FILE)


def load_searches() -> List[dict]:
    p = searches_path()
    if not os.path.exists(p):
        return _default_searches()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return _default_searches()


def save_searches(searches: List[dict]) -> None:
    with open(searches_path(), "w", encoding="utf-8") as f:
        json.dump(searches, f, indent=2, ensure_ascii=False)


def _default_searches() -> List[dict]:
    """Built-in examples from the project brief."""
    return [
        {"query": "Dental Clinics",   "city": "Asyut",     "country": "Egypt"},
        {"query": "Car Dealerships",  "city": "Cairo",     "country": "Egypt"},
        {"query": "Software Companies","city": "Alexandria","country": "Egypt"},
        {"query": "Construction Companies","city": "Giza", "country": "Egypt"},
        {"query": "Logistics Companies","city": "",         "country": "Egypt"},
    ]


# ---------------------------------------------------------------------------
# Job logic
# ---------------------------------------------------------------------------
def run_scheduled_scan(label: str = "scheduled", db: Optional[Database] = None) -> dict:
    """Execute every saved search once and emit a daily report."""
    db = db or Database()
    pipe = Pipeline(db=db)
    summary = {"label": label, "started_at": datetime.now().isoformat(timespec="seconds"),
               "results": [], "totals": {"inserted": 0, "updated": 0,
                                          "unchanged": 0, "failed": 0}}
    log.info("=== scheduled scan start (%s) ===", label)
    for entry in load_searches():
        try:
            crit = SearchCriteria(
                query=entry.get("query", ""),
                industry=entry.get("industry", ""),
                category=entry.get("category", ""),
                city=entry.get("city", ""),
                governorate=entry.get("governorate", ""),
                country=entry.get("country", "") or get_config().default_country,
                radius_km=entry.get("radius_km"),
                limit=int(entry.get("limit", 50)),
            )
            result = pipe.run(crit)
            summary["results"].append({
                "query": crit.query, "city": crit.city,
                "discovered": result.discovered, "inserted": result.inserted,
                "updated": result.updated, "duration": round(result.duration_sec, 1),
            })
            for k in ("inserted", "updated", "unchanged", "failed"):
                summary["totals"][k] += getattr(result, k)
        except Exception as e:
            log.exception("scheduled search failed for %r: %s", entry, e)
            summary["totals"]["failed"] += 1
    # Emit a report regardless of partial failures
    try:
        txt, jsn, report = generate_and_save(db, days=1, label="Daily")
        summary["report"] = txt
    except Exception as e:
        log.warning("report generation failed: %s", e)
    log.info("=== scheduled scan done (%s): %s ===", label, summary["totals"])
    _append_history(summary)
    return summary


def _append_history(summary: dict) -> None:
    cfg = get_config()
    hist_path = os.path.join(cfg.reports_dir, "scan_history.json")
    os.makedirs(cfg.reports_dir, exist_ok=True)
    history = []
    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        pass
    history.append(summary)
    history = history[-200:]  # cap
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
PRESETS = {
    "daily":   {"trigger": "cron", "hour": 2, "minute": 0},
    "weekly":  {"trigger": "cron", "day_of_week": "mon", "hour": 3, "minute": 0},
    "monthly": {"trigger": "cron", "day": 1, "hour": 4, "minute": 0},
}


class Scheduler:
    """Thin wrapper around APScheduler with friendly preset names."""

    def __init__(self):
        self._sched = BackgroundScheduler(daemon=True)
        self._jobs = {}

    def start(self) -> None:
        if not self._sched.running:
            self._sched.start()
            log.info("scheduler started")

    def shutdown(self, wait: bool = True) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=wait)

    def schedule(self, preset: str) -> None:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset '{preset}'")
        if preset in self._jobs:
            self.remove(preset)
        kwargs = dict(PRESETS[preset])
        kwargs.pop("trigger")
        self._jobs[preset] = self._sched.add_job(
            run_scheduled_scan, CronTrigger(**kwargs),
            args=[preset], id=preset, replace_existing=True,
            misfire_grace_time=3600)
        log.info("scheduled '%s' scan: %s", preset, PRESETS[preset])

    def remove(self, preset: str) -> None:
        if preset in self._jobs:
            try:
                self._jobs[preset].remove()
            except Exception:
                pass
            del self._jobs[preset]

    def jobs_info(self) -> list[dict]:
        out = []
        for name, job in self._jobs.items():
            next_run = job.next_run_time
            out.append({
                "preset": name,
                "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else None,
                "schedule": PRESETS.get(name, {}),
            })
        return out

    def run_now(self, preset: str = "manual") -> dict:
        return run_scheduled_scan(label=preset)


# Module-level singleton for the dashboard / CLI to share
_singleton: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    global _singleton
    if _singleton is None:
        _singleton = Scheduler()
    return _singleton
