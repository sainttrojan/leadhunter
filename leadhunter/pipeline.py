"""
Pipeline orchestrator.

End-to-end lead generation for a single search:

    search_query  ──►  discover candidates  ──►  enrich each site
                                                      │
                                                      ▼
                                              dedup + score
                                                      │
                                                      ▼
                                          store (SQLite) + export

Sources, in order of breadth→depth:
  1. OpenStreetMap Overpass (geo-targeted, structured business data)
  2. DuckDuckGo + Google (broad web discovery)
  3. Public directories (YellowPages, HotFrog)
  4. Per-website enrichment (emails, phones, social, about)

The orchestrator is deliberately synchronous and resilient — each step is
isolated so one failing source never aborts the whole run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from .config import get_config
from .core.database import Database
from .core.models import Lead
from .scrapers import (
    DirectoryScraper, OverpassScraper,
    SearchEngineScraper, WebsiteScraper)
from .utils.logger import get_logger
from .utils.scoring import compute_confidence, confidence_tier
from .utils.text import clean_text, normalize_domain, normalize_url

log = get_logger(__name__)


@dataclass
class SearchCriteria:
    """User-facing search spec. All fields optional except `query`."""
    query: str                            # e.g. "Dental Clinics"
    industry: str = ""
    category: str = ""
    city: str = ""
    governorate: str = ""
    country: str = ""
    radius_km: Optional[int] = None
    limit: int = 50
    enrich: bool = True                   # visit each website for deep info
    sources: List[str] = field(default_factory=lambda: [
        "openstreetmap", "search", "directories"])


@dataclass
class RunResult:
    criteria: SearchCriteria
    discovered: int = 0
    enriched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    duration_sec: float = 0.0
    leads: List[Lead] = field(default_factory=list)

    def summary(self) -> str:
        return (f"discovered={self.discovered} enriched={self.enriched} "
                f"inserted={self.inserted} updated={self.updated} "
                f"unchanged={self.unchanged} failed={self.failed} "
                f"in {self.duration_sec:.1f}s")


class Pipeline:
    def __init__(self, db: Optional[Database] = None):
        self.cfg = get_config()
        self.db = db or Database()
        # One client per source so each gets its own session/headers
        self.search = SearchEngineScraper()
        self.maps = OverpassScraper()
        self.directory = DirectoryScraper()
        self.website = WebsiteScraper()

    # ------------------------------------------------------------------
    def run(self, criteria: SearchCriteria) -> RunResult:
        start = time.monotonic()
        log.info("pipeline start: %r", criteria)
        country = criteria.country or self.cfg.default_country
        place = ", ".join(p for p in (criteria.governorate, criteria.city) if p) \
            or criteria.governorate or criteria.city

        # ---- 1. Discovery -------------------------------------------------
        discovered: List[Lead] = []
        if "openstreetmap" in criteria.sources and self.cfg.source_openstreetmap:
            discovered += self._from_osm(criteria, country, place)
        if "search" in criteria.sources and self.cfg.source_search_engines:
            discovered += self._from_search(criteria, country, place)
        if "directories" in criteria.sources and self.cfg.source_directories:
            discovered += self._from_directories(criteria, country, place)

        discovered = self._dedup_candidates(discovered)
        # Cap total candidates to keep the run bounded
        max_sites = self.cfg.max_sites_per_run
        if len(discovered) > max_sites:
            log.info("capping candidates %d -> %d", len(discovered), max_sites)
            discovered = discovered[:max_sites]

        # ---- 2. Enrichment ------------------------------------------------
        if criteria.enrich:
            discovered = self._enrich_all(discovered, criteria)

        # ---- 3. Persist ---------------------------------------------------
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "failed": 0}
        for lead in discovered:
            try:
                lead = self._finalize_lead(lead, criteria)
                _, action = self.db.upsert_lead(lead)
                counts[action] = counts.get(action, 0) + 1
            except Exception as e:
                counts["failed"] += 1
                log.warning("persist failed for %s: %s",
                            lead.company_name or lead.website, e)

        result = RunResult(
            criteria=criteria,
            discovered=len(discovered),
            inserted=counts["inserted"],
            updated=counts["updated"],
            unchanged=counts["unchanged"],
            failed=counts["failed"],
            duration_sec=time.monotonic() - start,
            leads=discovered,
        )
        log.info("pipeline done: %s", result.summary())
        return result

    # ------------------------------------------------------------------
    # Source adapters — each returns a list[Lead]
    # ------------------------------------------------------------------
    def _from_osm(self, c: SearchCriteria, country: str, place: str) -> List[Lead]:
        if not place:
            return []
        try:
            kw = c.query or c.industry or c.category
            return self.maps.search_area(
                query=kw, place=place, country=country,
                limit=c.limit, radius_km=c.radius_km)
        except Exception as e:
            log.warning("OSM source failed: %s", e)
            return []

    def _from_search(self, c: SearchCriteria, country: str, place: str) -> List[Lead]:
        try:
            q = self._build_query_string(c, country, place)
            urls = self.search.search(q, limit=c.limit)
            leads: List[Lead] = []
            for u in urls:
                lead = Lead(
                    website=u, source_url=u,
                    city=c.city, governorate=c.governorate, country=country,
                    industry=c.industry, category=c.category or c.query,
                    description=f"Discovered via search for '{c.query}'.")
                lead.confidence_score = compute_confidence(lead)
                lead.confidence_tier = confidence_tier(lead.confidence_score)
                leads.append(lead)
            return leads
        except Exception as e:
            log.warning("search source failed: %s", e)
            return []

    def _from_directories(self, c: SearchCriteria, country: str, place: str) -> List[Lead]:
        try:
            return self.directory.search(
                query=c.query or c.industry, place=place or c.city,
                country=country, limit=c.limit)
        except Exception as e:
            log.warning("directory source failed: %s", e)
            return []

    # ------------------------------------------------------------------
    def _build_query_string(self, c: SearchCriteria, country: str, place: str) -> str:
        parts = [c.query or c.industry or c.category]
        if c.category and c.category.lower() not in (parts[0] or "").lower():
            parts.append(c.category)
        if place:
            parts.append(place)
        if country and (not place or country.lower() not in place.lower()):
            parts.append(country)
        return " ".join(p for p in parts if p)

    def _dedup_candidates(self, leads: List[Lead]) -> List[Lead]:
        """Collapse duplicate candidates within a single run."""
        seen_domain, seen_name, out = set(), set(), []
        for lead in leads:
            domain = normalize_domain(lead.website)
            name = clean_text(lead.company_name).lower()
            if domain and domain in seen_domain:
                # Merge into existing record on the same domain
                for existing in out:
                    if normalize_domain(existing.website) == domain:
                        existing.merge(lead)
                        break
                continue
            if name and name in seen_name and not domain:
                continue
            if domain:
                seen_domain.add(domain)
            if name:
                seen_name.add(name)
            out.append(lead)
        return out

    def _enrich_all(self, leads: List[Lead], c: SearchCriteria) -> List[Lead]:
        enriched = []
        for lead in leads:
            if not lead.website:
                # Keep directory/OSM-only leads as-is; they're already structured.
                enriched.append(lead)
                continue
            try:
                self.website.enrich(lead)
                enriched.append(lead)
            except Exception as e:
                log.warning("enrich error on %s: %s", lead.website, e)
                enriched.append(lead)
        return enriched

    def _finalize_lead(self, lead: Lead, c: SearchCriteria) -> Lead:
        """Final normalization + field backfill before persistence."""
        lead.website = normalize_url(lead.website)
        # Backfill location from the search criteria when the lead lacks it
        if not lead.city and c.city:
            lead.city = c.city
        if not lead.governorate and c.governorate:
            lead.governorate = c.governorate
        if not lead.country:
            lead.country = c.country or self.cfg.default_country
        if not lead.industry and c.industry:
            lead.industry = c.industry
        if not lead.category and (c.category or c.query):
            lead.category = c.category or c.query
        lead.company_name = clean_text(lead.company_name) or (
            normalize_domain(lead.website) or "Unknown Business")
        # Always recompute score & tier with final data
        lead.confidence_score = compute_confidence(lead)
        lead.confidence_tier = confidence_tier(lead.confidence_score)
        return lead
