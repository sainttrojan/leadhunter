"""
Export leads to CSV and Excel.

Both functions write to <base>/exports/<timestamped_name>.<ext> and return
the file path. A pandas DataFrame is used as the common intermediary so
every consumer gets consistent column ordering.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Iterable, List, Optional

import pandas as pd

from ..config import get_config
from ..utils.logger import get_logger
from .models import Lead, LEAD_FIELD_ORDER

log = get_logger(__name__)

# Export column order — excludes internal bookkeeping fields.
_EXPORT_COLUMNS = [
    c for c in LEAD_FIELD_ORDER
    if c not in ("lead_id", "dedup_key", "discovered_at", "updated_at")
]


def leads_to_dataframe(leads: Iterable[Lead]) -> pd.DataFrame:
    rows = []
    for lead in leads:
        d = {c: getattr(lead, c, "") for c in _EXPORT_COLUMNS}
        rows.append(d)
    df = pd.DataFrame(rows, columns=_EXPORT_COLUMNS)
    return df


def _ensure_export_dir() -> str:
    cfg = get_config()
    os.makedirs(cfg.export_dir, exist_ok=True)
    return cfg.export_dir


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def export_csv(leads: Iterable[Lead], filename: Optional[str] = None) -> str:
    df = leads_to_dataframe(leads)
    name = filename or f"leads_{_timestamp()}.csv"
    path = os.path.join(_ensure_export_dir(), name)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("exported %d leads -> CSV: %s", len(df), path)
    return path


def export_excel(leads: Iterable[Lead], filename: Optional[str] = None) -> str:
    df = leads_to_dataframe(leads)
    name = filename or f"leads_{_timestamp()}.xlsx"
    path = os.path.join(_ensure_export_dir(), name)
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Leads", index=False)
        wb = writer.book
        ws = writer.sheets["Leads"]
        # Header formatting + auto column width
        fmt = wb.add_format({"bold": True, "bg_color": "#1f77b4",
                             "font_color": "#FFFFFF", "border": 1})
        for i, col in enumerate(df.columns):
            max_len = max(
                [len(str(col))] +
                [len(str(v)) for v in df[col].head(200).tolist()])
            ws.set_column(i, i, min(max_len + 2, 60))
        ws.set_row(0, 20, fmt)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, max(len(df), 0), len(df.columns) - 1)
    log.info("exported %d leads -> Excel: %s", len(df), path)
    return path
