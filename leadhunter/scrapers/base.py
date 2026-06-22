"""
Base HTTP client — production-grade resilience for scraping.

Features:
  * Retries with exponential backoff on 429 / 5xx / connection errors
  * Per-host rate limiting (min_delay..max_delay jitter)
  * Rotating proxy support (round-robin)
  * Realistic browser headers
  * Centralized error handling + logging
  * Polite robots-style skipping of obviously-blocked responses

Every scraper subclasses this so resilience lives in exactly one place.
"""
from __future__ import annotations

import itertools
import random
import time
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from ..config import Config, get_config
from ..utils.logger import get_logger

log = get_logger(__name__)

# Treat these as permanent failures (no retry)
_PERMANENT_STATUS = {400, 401, 403, 404, 410}
# Retryable HTTP statuses
_RETRY_STATUS = {429, 500, 502, 503, 504}


class BaseHTTPClient:
    def __init__(self, config: Optional[Config] = None, session: Optional[requests.Session] = None):
        self.cfg = config or get_config()
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": self.cfg.user_agent,
            **self.cfg.extra_headers,
        })
        self._last_request_at: Dict[str, float] = {}
        self._proxy_cycle = itertools.cycle(self.cfg.proxies) if self.cfg.proxies else None
        self._request_count = 0

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    def _respect_rate_limit(self, host: str) -> None:
        last = self._last_request_at.get(host, 0.0)
        elapsed = time.monotonic() - last
        wait = random.uniform(self.cfg.min_delay, self.cfg.max_delay)
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_request_at[host] = time.monotonic()

    def _next_proxy(self) -> Optional[Dict[str, str]]:
        if not self.cfg.proxy_enabled or not self._proxy_cycle:
            return None
        proxy_url = next(self._proxy_cycle)
        return {"http": proxy_url, "https": proxy_url}

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------
    def get(self, url: str, *, timeout: Optional[int] = None,
            retries: Optional[int] = None,
            allow_redirects: bool = True,
            extra_headers: Optional[dict] = None) -> Optional[requests.Response]:
        """GET with retries/backoff. Returns a Response, or None on failure."""
        timeout = timeout or self.cfg.request_timeout
        retries = self.cfg.max_retries if retries is None else retries
        host = urlparse(url).netloc or "unknown"

        headers = dict(self.session.headers)
        if extra_headers:
            headers.update(extra_headers)

        attempt = 0
        while attempt <= retries:
            attempt += 1
            try:
                self._respect_rate_limit(host)
                self._request_count += 1
                resp = self.session.get(
                    url, timeout=timeout, headers=headers,
                    allow_redirects=allow_redirects,
                    proxies=self._next_proxy())
                if resp.status_code in _PERMANENT_STATUS:
                    log.debug("permanent %s for %s", resp.status_code, url)
                    return resp
                if resp.status_code in _RETRY_STATUS and attempt <= retries:
                    backoff = self.cfg.backoff_factor ** attempt + random.uniform(0, 0.5)
                    log.warning("retryable %s for %s (attempt %d) — backoff %.1fs",
                                resp.status_code, url, attempt, backoff)
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.SSLError as e:
                log.warning("SSL error %s: %s", url, e)
                # Retry once without verify (common on small business sites)
                if attempt == 1:
                    try:
                        self._respect_rate_limit(host)
                        resp = self.session.get(url, timeout=timeout, verify=False,
                                                headers=headers, allow_redirects=allow_redirects,
                                                proxies=self._next_proxy())
                        resp.raise_for_status()
                        return resp
                    except Exception:
                        pass
                return None
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if attempt > retries:
                    log.warning("giving up on %s after %d attempts: %s",
                                url, attempt, e)
                    return None
                backoff = self.cfg.backoff_factor ** attempt + random.uniform(0, 0.5)
                log.warning("conn/timeout for %s (attempt %d) — backoff %.1fs",
                            url, attempt, backoff)
                time.sleep(backoff)
            except requests.exceptions.HTTPError as e:
                log.warning("HTTP error %s: %s", url, e)
                return None
            except Exception as e:
                log.exception("unexpected error fetching %s: %s", url, e)
                return None
        return None

    def get_text(self, url: str, **kwargs) -> Optional[str]:
        resp = self.get(url, **kwargs)
        if resp is None:
            return None
        # Detect HTML charset properly
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    def get_json(self, url: str, **kwargs):
        resp = self.get(url, **kwargs)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # ------------------------------------------------------------------
    @property
    def request_count(self) -> int:
        return self._request_count
