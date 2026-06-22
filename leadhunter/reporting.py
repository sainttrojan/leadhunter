"""
Daily/weekly reporting.

Generates text + JSON reports summarizing:
  * New leads added in the period
  * Leads updated in the period
  * Leads missing key contact info (email/phone)

Reports are written to <base>/reports/ as both .txt and .json, and a
summary string is returned for inline use (CLI / dashboard).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import get_config
from .core.database import Database
from .core.models import Lead
from .utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ReportData:
    period_label: str           # "Daily" / "Weekly"
    period_start: str           # ISO timestamp
    generated_at: str
    totals: dict                # raw counts
    top_industries: dict
    top_cities: dict
    sample_new: list            # list of dicts (company / score)
    sample_missing_contact: list

    def to_dict(self) -> dict:
        return {
            "period_label": self.period_label,
            "period_start": self.period_start,
            "generated_at": self.generated_at,
            "totals": self.totals,
            "top_industries": self.top_industries,
            "top_cities": self.top_cities,
            "sample_new": self.sample_new,
            "sample_missing_contact": self.sample_missing_contact,
        }


def _now_iso() -> str:
    # timezone-aware UTC (Python 3.14 deprecates datetime.utcnow)
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_report(db: Database, *, days: int = 1,
                 label: str = "Daily") -> ReportData:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    totals = db.counts_since(since)

    # Top industries / cities among NEW leads
    new_leads = _leads_since(db, since, field="discovered_at")
    top_industries = _top_values(new_leads, "industry", 5)
    top_cities = _top_values(new_leads, "city", 5)
    sample_new = [
        {"company": l.company_name, "city": l.city, "score": l.confidence_score}
        for l in sorted(new_leads, key=lambda x: x.confidence_score, reverse=True)[:10]
    ]
    all_missing = [l for l in db.get_all() if not l.email and not l.phone]
    sample_missing = [
        {"company": l.company_name, "city": l.city, "website": l.website}
        for l in all_missing[:10]
    ]

    return ReportData(
        period_label=label,
        period_start=since,
        generated_at=_now_iso(),
        totals=totals,
        top_industries=top_industries,
        top_cities=top_cities,
        sample_new=sample_new,
        sample_missing_contact=sample_missing,
    )


def write_report(report: ReportData) -> tuple[str, str]:
    """Write .txt + .json. Returns (txt_path, json_path)."""
    cfg = get_config()
    os.makedirs(cfg.reports_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{report.period_label.lower()}_report_{stamp}"
    txt_path = os.path.join(cfg.reports_dir, base + ".txt")
    json_path = os.path.join(cfg.reports_dir, base + ".json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(_format_text(report))
    log.info("report written: %s", txt_path)
    return txt_path, json_path


def _format_text(r: ReportData) -> str:
    t = r.totals
    lines = [
        f"LeadHunter — {r.period_label} Report",
        f"Period start : {r.period_start}",
        f"Generated at : {r.generated_at}",
        "=" * 50,
        "",
        "Totals",
        "-" * 50,
        f"  Total leads in DB        : {t.get('total', 0)}",
        f"  New leads this period    : {t.get('new', 0)}",
        f"  Updated this period      : {t.get('updated', 0)}",
        f"  Missing email            : {t.get('missing_email', 0)}",
        f"  Missing phone            : {t.get('missing_phone', 0)}",
        f"  Missing any contact      : {t.get('missing_contact', 0)}",
        "",
        "Top Industries (new)",
        "-" * 50,
    ]
    for k, v in list(r.top_industries.items())[:5]:
        lines.append(f"  {k or '(unknown)'}: {v}")
    lines += ["", "Top Cities (new)", "-" * 50]
    for k, v in list(r.top_cities.items())[:5]:
        lines.append(f"  {k or '(unknown)'}: {v}")
    lines += ["", "Sample New Leads", "-" * 50]
    for s in r.sample_new:
        lines.append(f"  - {s['company']}  [{s['city']}]  score={s['score']}")
    lines += ["", "Sample — Missing Contact Info", "-" * 50]
    for s in r.sample_missing_contact:
        lines.append(f"  - {s['company']}  [{s['city']}]  {s['website']}")
    lines.append("")
    return "\n".join(lines)


def _leads_since(db: Database, since_iso: str, *, field: str = "discovered_at") -> list[Lead]:
    # The DAL doesn't expose a native "since" filter; reuse get_all + filter.
    out = []
    for l in db.get_all():
        val = getattr(l, field, "") or ""
        if val and val >= since_iso:
            out.append(l)
    return out


def _top_values(leads: list[Lead], attr: str, n: int = 5) -> dict:
    counts: dict = {}
    for l in leads:
        v = (getattr(l, attr, "") or "").strip()
        if not v:
            continue
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n])


def generate_and_save(db: Optional[Database] = None, *, days: int = 1,
                      label: Optional[str] = None) -> tuple[str, str, ReportData]:
    """Convenience: build + write a report. Returns (txt, json, report)."""
    db = db or Database()
    label = label or ("Weekly" if days >= 7 else "Daily")
    report = build_report(db, days=days, label=label)
    txt, jsn = write_report(report)
    return txt, jsn, report
