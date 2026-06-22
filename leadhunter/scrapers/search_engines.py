"""
Search-engine scraper (no API keys required).

Uses the lightweight HTML endpoints:
  * DuckDuckGo HTML   (html.duckduckgo.com/html)   — primary, via POST
  * Google search     (www.google.com/search)       — fallback
  * Bing search       (www.bing.com/search)          — second fallback

Returns a list of normalized candidate URLs.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

from bs4 import BeautifulSoup

from ..config import get_config
from ..utils.logger import get_logger
from ..utils.text import normalize_url
from .base import BaseHTTPClient

log = get_logger(__name__)


class SearchEngineScraper(BaseHTTPClient):
    # Hosts that aren't businesses — excluded from results
    _JUNK_HOSTS = {
        "youtube.com", "facebook.com", "twitter.com", "x.com", "wikipedia.org",
        "instagram.com", "linkedin.com", "tiktok.com", "pinterest.com",
        "reddit.com", "amazon.", "play.google.com", "apps.apple.com",
        "yelp.com", "tripadvisor.", "duckduckgo.com", "google.com",
        "bing.com", "yahoo.com", "maps.google", "maps.app.goo.gl",
        "pinterest.", "quora.com", "medium.com", "ebay.com", "aliexpress.",
        "wikipedia.", "openstreetmap.org",
    }

    def search(self, query: str, limit: int = 25) -> List[str]:
        """Run a search and return up to `limit` unique, business-y URLs."""
        limit = limit or get_config().max_search_results
        results: List[str] = []

        # Source 1: DuckDuckGo (POST, no JS needed)
        try:
            results = self._search_duckduckgo(query, limit) or []
        except Exception as e:
            log.warning("DuckDuckGo search failed: %s", e)

        # Source 2: Google (if we need more)
        if len(results) < limit // 2:
            try:
                google = self._search_google(query, limit) or []
                for u in google:
                    if u not in results:
                        results.append(u)
            except Exception as e:
                log.warning("Google search failed: %s", e)

        # Source 3: Bing (last resort)
        if len(results) < limit // 4:
            try:
                bing = self._search_bing(query, limit) or []
                for u in bing:
                    if u not in results:
                        results.append(u)
            except Exception as e:
                log.warning("Bing search failed: %s", e)

        # Filter and cap
        out: List[str] = []
        for u in results:
            if u and u not in out and self._is_business_host(u):
                out.append(u)
            if len(out) >= limit:
                break
        log.info("search '%s' -> %d candidate URLs", query, len(out))
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _is_business_host(url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        if not host:
            return False
        return not any(j in host for j in SearchEngineScraper._JUNK_HOSTS)

    # ------------------------------------------------------------------
    # DuckDuckGo — always POST to the HTML endpoint
    # ------------------------------------------------------------------
    def _search_duckduckgo(self, query: str, limit: int) -> List[str]:
        url = "https://html.duckduckgo.com/html/"
        resp = self._post(url, {"q": query, "b": ""})
        if resp is None:
            return []
        return self._parse_duckduckgo(resp.text, limit)

    def _post(self, url: str, data: dict):
        host = urlparse(url).netloc
        self._respect_rate_limit(host)
        try:
            resp = self.session.post(
                url, data=data, timeout=self.cfg.request_timeout,
                headers={
                    **dict(self.session.headers),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "https://duckduckgo.com/",
                    "Origin": "https://duckduckgo.com",
                },
                proxies=self._next_proxy())
            if resp.status_code in (403, 429):
                log.warning("DDG returned %d — possibly blocked", resp.status_code)
                return None
            return resp
        except Exception as e:
            log.warning("POST %s failed: %s", url, e)
            return None

    @staticmethod
    def _parse_duckduckgo(html: str, limit: int) -> List[str]:
        out: List[str] = []
        soup = BeautifulSoup(html, "lxml")
        # Primary: <a class="result__a" href="...">
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            real = SearchEngineScraper._unwrap_ddg_href(href)
            if real:
                norm = normalize_url(real)
                if norm and norm not in out:
                    out.append(norm)
            if len(out) >= limit:
                break
        # Fallback: scrape ALL links that look external
        if not out:
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                real = SearchEngineScraper._unwrap_ddg_href(href)
                if real and real.startswith("http"):
                    norm = normalize_url(real)
                    if norm and norm not in out:
                        out.append(norm)
                if len(out) >= limit:
                    break
        return out

    @staticmethod
    def _unwrap_ddg_href(href: str) -> str:
        if "uddg=" in href:
            qs = parse_qs(href.split("?", 1)[-1] if "?" in href else href)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        if href.startswith("//duckduckgo.com/l/"):
            return ""
        if href.startswith("//"):
            return "https:" + href
        return href

    # ------------------------------------------------------------------
    # Google — GET, scrape /url?q= redirect links
    # ------------------------------------------------------------------
    def _search_google(self, query: str, limit: int) -> List[str]:
        url = ("https://www.google.com/search?q=" + quote_plus(query) +
               "&num=" + str(min(limit + 5, 50)) + "&hl=en")
        text = self.get_text(url, extra_headers={
            "Referer": "https://www.google.com/",
            "Accept": "text/html,application/xhtml+xml",
        })
        if not text:
            return []
        return self._parse_google_results(text, limit)

    @staticmethod
    def _parse_google_results(html: str, limit: int) -> List[str]:
        out: List[str] = []
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            real = href
            if href.startswith("/url?"):
                qs = parse_qs(href.split("?", 1)[-1])
                real = qs.get("q", [""])[0]
            if real.startswith("http") and "google." not in real:
                norm = normalize_url(real)
                if norm and norm not in out:
                    out.append(norm)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    # Bing — GET, scrape <li class="b_algo"><h2><a href="...">
    # ------------------------------------------------------------------
    def _search_bing(self, query: str, limit: int) -> List[str]:
        url = ("https://www.bing.com/search?q=" + quote_plus(query) +
               "&count=" + str(min(limit + 5, 50)) + "&setlang=en")
        text = self.get_text(url, extra_headers={
            "Referer": "https://www.bing.com/",
            "Accept": "text/html,application/xhtml+xml",
        })
        if not text:
            return []
        out: List[str] = []
        soup = BeautifulSoup(html, "lxml") if (html := text) else None
        if not soup:
            return []
        for a in soup.select("li.b_algo h2 a[href], .b_algo h2 a[href]"):
            href = a.get("href", "")
            if href.startswith("http"):
                norm = normalize_url(href)
                if norm and norm not in out:
                    out.append(norm)
            if len(out) >= limit:
                break
        # Broader fallback
        if not out:
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if href.startswith("http") and "bing." not in href and "microsoft." not in href:
                    norm = normalize_url(href)
                    if norm and norm not in out:
                        out.append(norm)
                if len(out) >= limit:
                    break
        return out
