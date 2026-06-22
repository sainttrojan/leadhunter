"""
Public business-directory scraper.

Aggregates candidate business listings from directories that publish plain
HTML (no API key, no JS-walled-garden). The strategy is defensive: each
adapter returns a list of lightweight candidate URLs / company names, and
the pipeline's WebsiteScraper does the deep enrichment.

Currently supported (all key-less, public):
  * YellowPages.com (global)      — listing pages link out to company sites
  * HotFrog.com                    — international business directory
  * Generic site:query augmentation — for any other indexed directory

Adding a new directory = add a method here returning (name, url) tuples.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from ..config import get_config
from ..core.models import Lead
from ..utils.logger import get_logger
from ..utils.scoring import compute_confidence, confidence_tier
from ..utils.text import clean_text, normalize_url
from .base import BaseHTTPClient

log = get_logger(__name__)


class DirectoryScraper(BaseHTTPClient):
    def search(self, *, query: str, place: str, country: str = "Egypt",
               limit: int = 30) -> List[Lead]:
        """Run across all configured directory adapters."""
        leads: List[Lead] = []
        seen: set = set()

        for adapter in (self._yellowpages, self._hotfrog):
            try:
                for name, url in adapter(query, place, country):
                    key = (name.lower(), url.lower())
                    if not name or key in seen:
                        continue
                    seen.add(key)
                    lead = self._quick_lead(name, url, query, place, country)
                    if lead:
                        leads.append(lead)
                    if len(leads) >= limit:
                        return leads
            except Exception as e:
                log.warning("directory adapter %s failed: %s",
                            adapter.__name__, e)
        log.info("directories '%s' in %s -> %d candidates", query, place, len(leads))
        return leads

    # ------------------------------------------------------------------
    # Adapters — each returns a list of (company_name, website_url) tuples.
    # ------------------------------------------------------------------
    def _yellowpages(self, query: str, place: str, country: str) -> List[Tuple[str, str]]:
        url = ("https://www.yellowpages.com/search?search_terms="
               + quote_plus(query) + "&geo_location_terms=" + quote_plus(place))
        text = self.get_text(url)
        if not text:
            return []
        return self._parse_yellowpages(text)

    @staticmethod
    def _parse_yellowpages(html: str) -> List[Tuple[str, str]]:
        out = []
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select(".result, .search-result, .listing"):
            name_el = card.select_one(".business-name, a.business-name, h3 a")
            name = clean_text(name_el.get_text()) if name_el else ""
            site_el = card.select_one("a.track-visit-website, a.website, a.result-website")
            site = normalize_url(site_el.get("href")) if site_el else ""
            if name and site:
                out.append((name, site))
        return out

    def _hotfrog(self, query: str, place: str, country: str) -> List[Tuple[str, str]]:
        url = ("https://www.hotfrog.com/search/" + quote_plus(place)
               + "/" + quote_plus(query))
        text = self.get_text(url)
        if not text:
            return []
        return self._parse_generic(text, base="https://www.hotfrog.com")

    @staticmethod
    def _parse_generic(html: str, base: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            text = clean_text(a.get_text())
            if not text or len(text) < 3:
                continue
            full = normalize_url(href, base)
            if not full:
                continue
            host = full.split("//")[-1].split("/")[0].lower()
            # Skip links that point back to the directory itself
            if base.replace("https://", "").replace("http://", "").split("/")[0] in host:
                continue
            if any(s in host for s in ("facebook.", "twitter.", "instagram.",
                                       "linkedin.", "youtube.", "google.")):
                continue
            out.append((text, full))
        return out

    # ------------------------------------------------------------------
    def _quick_lead(self, name: str, url: str, query: str, place: str,
                    country: str) -> Optional[Lead]:
        from ..utils.text import guess_industry_from_keywords
        industry = guess_industry_from_keywords(f"{query} {name}") or "Other"
        lead = Lead(
            company_name=name, website=url, city=place, country=country,
            industry=industry, category=clean_text(query),
            source_url=url,
            description=f"Found via business directory for '{query}' in {place}.",
        )
        lead.confidence_score = compute_confidence(lead)
        lead.confidence_tier = confidence_tier(lead.confidence_score)
        return lead
