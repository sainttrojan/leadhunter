"""
SQLite data-access layer for LeadHunter.

Single table `leads` mirrors the Lead dataclass. The class offers:
  - upsert_lead          — insert-or-update with dedup
  - get_lead, get_all    — fetch by id / list
  - search / filter      — query by industry/city/country/score/source
  - stats                — aggregates for the dashboard
  - counts_since         — for daily reports (new/updated)

All writes go through `upsert_lead`, which builds a deterministic dedup_key
(website domain if available, else normalized name+city+country).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from ..config import get_config
from ..utils.logger import get_logger
from ..utils.text import normalize_domain
from .models import Lead, LEAD_FIELD_ORDER

log = get_logger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name        TEXT NOT NULL DEFAULT '',
    industry            TEXT DEFAULT '',
    category            TEXT DEFAULT '',
    website             TEXT DEFAULT '',
    email               TEXT DEFAULT '',
    phone               TEXT DEFAULT '',
    whatsapp            TEXT DEFAULT '',
    address             TEXT DEFAULT '',
    city                TEXT DEFAULT '',
    governorate         TEXT DEFAULT '',
    country             TEXT DEFAULT '',
    maps_link           TEXT DEFAULT '',
    linkedin_url        TEXT DEFAULT '',
    facebook_url        TEXT DEFAULT '',
    instagram_url       TEXT DEFAULT '',
    employees           TEXT DEFAULT '',
    description         TEXT DEFAULT '',
    contact_person      TEXT DEFAULT '',
    source_url          TEXT DEFAULT '',
    confidence_score    INTEGER DEFAULT 0,
    confidence_tier     TEXT DEFAULT '',
    dedup_key           TEXT UNIQUE,
    discovered_at       TEXT DEFAULT '',
    updated_at          TEXT DEFAULT ''
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_leads_industry ON leads(industry);",
    "CREATE INDEX IF NOT EXISTS idx_leads_city     ON leads(city);",
    "CREATE INDEX IF NOT EXISTS idx_leads_country  ON leads(country);",
    "CREATE INDEX IF NOT EXISTS idx_leads_score    ON leads(confidence_score);",
    "CREATE INDEX IF NOT EXISTS idx_leads_source   ON leads(source_url);",
    "CREATE INDEX IF NOT EXISTS idx_leads_discover ON leads(discovered_at);",
    "CREATE INDEX IF NOT EXISTS idx_leads_email    ON leads(email);",
    "CREATE INDEX IF NOT EXISTS idx_leads_phone    ON leads(phone);",
]

# Columns we persist (everything except lead_id, which is auto).
COLUMNS = [c for c in LEAD_FIELD_ORDER if c != "lead_id"]


def _now_iso() -> str:
    # timezone-aware UTC, ISO-8601 (Python 3.14 deprecates datetime.utcnow)
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dedup_key(lead: Lead) -> str:
    """Deterministic uniqueness key for a lead."""
    domain = normalize_domain(lead.website)
    if domain:
        return "dom:" + domain
    name = (lead.company_name or "").strip().lower()
    city = (lead.city or "").strip().lower()
    country = (lead.country or "").strip().lower()
    phone = (lead.phone or "").replace(" ", "")
    parts = [p for p in (name, city, country, phone) if p]
    return "name:" + "|".join(parts) or "anon:" + _now_iso()


class Database:
    def __init__(self, db_path: Optional[str] = None):
        cfg = get_config()
        self.db_path = db_path or cfg.db_path
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            for stmt in INDEXES:
                conn.execute(stmt)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def upsert_lead(self, lead: Lead) -> tuple[Lead, str]:
        """Insert or update a lead. Returns (lead, action) where action is
        'inserted' | 'updated' | 'unchanged'."""
        lead.dedup_key = lead.dedup_key or _dedup_key(lead)
        if not lead.discovered_at:
            lead.discovered_at = _now_iso()

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM leads WHERE dedup_key=?", (lead.dedup_key,)).fetchone()

            if row is None:
                lead.updated_at = _now_iso()
                self._insert(conn, lead)
                log.debug("inserted lead: %s", lead.company_name or lead.dedup_key)
                return lead, "inserted"

            # Merge: keep existing, fill missing from incoming, update mutable fields
            existing = dict(row)
            changed = self._merge_into(conn, existing, lead)
            lead.lead_id = existing["lead_id"]
            action = "updated" if changed else "unchanged"
            if changed:
                log.debug("updated lead id=%s (%s)", lead.lead_id, lead.company_name)
            return lead, action

    def _insert(self, conn, lead: Lead) -> None:
        cols = ", ".join(COLUMNS)
        placeholders = ", ".join("?" for _ in COLUMNS)
        values = [self._col_value(lead, c) for c in COLUMNS]
        conn.execute(
            f"INSERT INTO leads ({cols}) VALUES ({placeholders});", values)
        # Fetch the assigned id
        row = conn.execute(
            "SELECT lead_id FROM leads WHERE dedup_key=?;",
            (lead.dedup_key,)).fetchone()
        if row:
            lead.lead_id = row["lead_id"]

    def _merge_into(self, conn, existing: dict, incoming: Lead) -> bool:
        """Update the DB row from `incoming`, only filling empty fields and
        refreshing mutable contact/scoring fields. Returns True if changed."""
        changed = False
        new_values = dict(existing)
        for c in COLUMNS:
            cur = existing.get(c) or ""
            inc = getattr(incoming, c, "") or ""
            if not inc:
                continue
            if c in ("confidence_score", "confidence_tier", "updated_at",
                     "discovered_at"):
                continue  # handled separately
            if not cur:
                new_values[c] = inc
                changed = True

        # Always recompute updated_at + score if any field changed.
        # Recompute score from the merged view.
        merged_lead = Lead(**{**{c: new_values.get(c, "") for c in LEAD_FIELD_ORDER
                                  if c != "lead_id"},
                              "lead_id": existing["lead_id"]})
        if merged_lead.confidence_score != existing.get("confidence_score", 0):
            changed = True
        if changed:
            new_values["confidence_score"] = merged_lead.confidence_score
            new_values["confidence_tier"] = merged_lead.confidence_tier
            new_values["updated_at"] = _now_iso()
            self._update_row(conn, existing["lead_id"], new_values)
        return changed

    def _update_row(self, conn, lead_id: int, values: dict) -> None:
        sets = ", ".join(f"{c}=?" for c in COLUMNS)
        conn.execute(
            f"UPDATE leads SET {sets} WHERE lead_id=?;",
            [self._coerce(values.get(c, "")) for c in COLUMNS] + [lead_id])

    @staticmethod
    def _coerce(v):
        if v is None:
            return ""
        return int(v) if isinstance(v, bool) or (isinstance(v, int)) else v

    @staticmethod
    def _col_value(lead: Lead, col: str):
        v = getattr(lead, col, "")
        if v is None:
            return ""
        return v

    def bulk_upsert(self, leads: Iterable[Lead]) -> dict:
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "failed": 0}
        for lead in leads:
            try:
                _, action = self.upsert_lead(lead)
                counts[action] = counts.get(action, 0) + 1
            except Exception as e:
                counts["failed"] += 1
                log.exception("upsert failed for %s: %s", lead.company_name, e)
        return counts

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def _row_to_lead(self, row) -> Lead:
        d = dict(row)
        # LEAD_FIELD_ORDER includes lead_id; exclude it from the spread so we
        # can pass it explicitly without "multiple values for argument".
        fields = {c: d.get(c, "") for c in LEAD_FIELD_ORDER if c != "lead_id"}
        return Lead(**fields, lead_id=d.get("lead_id"))

    def get_lead(self, lead_id: int) -> Optional[Lead]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM leads WHERE lead_id=?;", (lead_id,)).fetchone()
            return self._row_to_lead(row) if row else None

    def get_all(self, limit: Optional[int] = None) -> List[Lead]:
        with self._conn() as conn:
            q = "SELECT * FROM leads ORDER BY confidence_score DESC, company_name;"
            if limit:
                q = f"SELECT * FROM leads ORDER BY confidence_score DESC, company_name LIMIT {int(limit)};"
            rows = conn.execute(q).fetchall()
            return [self._row_to_lead(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM leads;").fetchone()[0]

    def search(self, *, industry: str = "", city: str = "",
               governorate: str = "", country: str = "", keyword: str = "",
               source: str = "", min_score: int = 0,
               limit: Optional[int] = None) -> List[Lead]:
        clauses, params = [], []
        if industry:
            clauses.append("(LOWER(industry)=LOWER(?) OR LOWER(category)=LOWER(?))")
            params += [industry, industry]
        if city:
            clauses.append("LOWER(city)=LOWER(?)")
            params.append(city)
        if governorate:
            clauses.append("LOWER(governorate)=LOWER(?)")
            params.append(governorate)
        if country:
            clauses.append("LOWER(country)=LOWER(?)")
            params.append(country)
        if source:
            clauses.append("(LOWER(source_url) LIKE LOWER(?))")
            params.append(f"%{source}%")
        if min_score:
            clauses.append("confidence_score >= ?")
            params.append(int(min_score))
        if keyword:
            kw = f"%{keyword.lower()}%"
            clauses.append(
                "(LOWER(company_name) LIKE ? OR LOWER(description) LIKE ? "
                "OR LOWER(category) LIKE ?)")
            params += [kw, kw, kw]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = ("SELECT * FROM leads" + where +
               " ORDER BY confidence_score DESC, company_name")
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            rows = conn.execute(sql + ";", params).fetchall()
            return [self._row_to_lead(r) for r in rows]

    def counts_since(self, since_iso: str) -> dict:
        """Aggregates for daily/weekly reports."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM leads;").fetchone()[0]
            new = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE discovered_at >= ?;",
                (since_iso,)).fetchone()[0]
            updated = conn.execute(
                "SELECT COUNT(*) FROM leads "
                "WHERE updated_at >= ? AND discovered_at < ?;",
                (since_iso, since_iso)).fetchone()[0]
            missing_email = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE email='';").fetchone()[0]
            missing_phone = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE phone='';").fetchone()[0]
            missing_contact = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE email='' AND phone='';"
            ).fetchone()[0]
        return {
            "total": total, "new": new, "updated": updated,
            "missing_email": missing_email, "missing_phone": missing_phone,
            "missing_contact": missing_contact,
        }

    def stats(self) -> dict:
        """Aggregates powering the dashboard."""
        out = {"total": 0, "by_industry": {}, "by_city": {}, "by_country": {},
               "by_source_host": {}, "by_tier": {}, "avg_score": 0.0}
        with self._conn() as conn:
            out["total"] = conn.execute("SELECT COUNT(*) FROM leads;").fetchone()[0]
            for label, col in (("by_industry", "industry"),
                               ("by_city", "city"),
                               ("by_country", "country"),
                               ("by_tier", "confidence_tier")):
                rows = conn.execute(
                    f"SELECT {col} AS k, COUNT(*) c FROM leads "
                    f"WHERE {col}<>'' GROUP BY {col} ORDER BY c DESC LIMIT 25;"
                ).fetchall()
                out[label] = {r["k"]: r["c"] for r in rows}
            # source -> host (rough)
            rows = conn.execute(
                "SELECT source_url AS k, COUNT(*) c FROM leads "
                "WHERE source_url<>'' GROUP BY source_url ORDER BY c DESC LIMIT 25;"
            ).fetchall()
            from urllib.parse import urlparse
            host_map: dict = {}
            for r in rows:
                try:
                    host = (urlparse(r["k"]).netloc or r["k"]).lower()
                    if host.startswith("www."):
                        host = host[4:]
                except Exception:
                    host = r["k"]
                host_map[host] = host_map.get(host, 0) + r["c"]
            out["by_source_host"] = dict(
                sorted(host_map.items(), key=lambda kv: kv[1], reverse=True)[:25])
            avg = conn.execute(
                "SELECT AVG(confidence_score) FROM leads;").fetchone()[0]
            out["avg_score"] = round(float(avg or 0), 1)
        return out

    def delete_lead(self, lead_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM leads WHERE lead_id=?;", (lead_id,))
