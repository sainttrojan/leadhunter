"""
Central configuration for the LeadHunter platform.

All tunable knobs (rate limits, retries, source toggles, default country,
DB path, export folder) live here. Override any value via environment
variables — `get_config()` resolves them at runtime.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "y", "on")


def _env_list(key: str, default: str) -> List[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Config:
    # --- Paths ---
    base_dir: str = ""
    db_path: str = ""
    export_dir: str = ""
    reports_dir: str = ""
    logs_dir: str = ""

    # --- Defaults (Egypt-focused, fully configurable) ---
    default_country: str = "Egypt"
    default_country_code: str = "EG"
    default_phone_region: str = "EG"

    # --- HTTP / networking ---
    request_timeout: int = 15
    max_retries: int = 3
    backoff_factor: float = 1.5  # seconds, exponential
    min_delay: float = 1.0       # min delay between requests to same host
    max_delay: float = 3.0       # max jitter delay
    max_concurrency: int = 4
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    extra_headers: dict = field(default_factory=lambda: {
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # --- Proxies (comma-separated: http://user:pass@host:port) ---
    proxies: List[str] = field(default_factory=list)
    proxy_enabled: bool = False

    # --- Source toggles ---
    source_search_engines: bool = True   # DuckDuckGo HTML (no API key)
    source_google: bool = True           # Google search (scrape)
    source_openstreetmap: bool = True    # Overpass API (Google Maps alternative)
    source_company_websites: bool = True # Visit discovered sites & enrich
    source_directories: bool = True      # public directory aggregators

    # --- Scoring weights ---
    score_website: int = 25
    score_email: int = 25
    score_phone: int = 20
    score_social: int = 15
    score_description: int = 15

    # --- Limits ---
    max_search_results: int = 50
    max_sites_per_run: int = 100
    enrich_fetch_pages: int = 4  # pages to fetch per website (home, about, contact)

    # --- Streamlit / dashboard ---
    dashboard_port: int = 8501


# Module-level default instance (paths filled lazily on first import)
_CONFIG: Config | None = None


def _resolve_paths(cfg: Config) -> Config:
    # base_dir = two levels up from this file -> project root that contains the package
    cfg.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg.db_path = os.environ.get(
        "LEADHUNTER_DB_PATH", os.path.join(cfg.base_dir, "data", "leads.db"))
    cfg.export_dir = os.environ.get(
        "LEADHUNTER_EXPORT_DIR", os.path.join(cfg.base_dir, "exports"))
    cfg.reports_dir = os.environ.get(
        "LEADHUNTER_REPORTS_DIR", os.path.join(cfg.base_dir, "reports"))
    cfg.logs_dir = os.environ.get(
        "LEADHUNTER_LOGS_DIR", os.path.join(cfg.base_dir, "logs"))
    return cfg


def _resolve_env(cfg: Config) -> Config:
    cfg.default_country = os.environ.get("LEADHUNTER_COUNTRY", cfg.default_country)
    cfg.default_country_code = os.environ.get("LEADHUNTER_COUNTRY_CODE", cfg.default_country_code)
    cfg.default_phone_region = os.environ.get("LEADHUNTER_PHONE_REGION", cfg.default_phone_region)

    cfg.request_timeout = _env_int("LEADHUNTER_TIMEOUT", cfg.request_timeout)
    cfg.max_retries = _env_int("LEADHUNTER_RETRIES", cfg.max_retries)
    cfg.min_delay = _env_float("LEADHUNTER_MIN_DELAY", cfg.min_delay)
    cfg.max_delay = _env_float("LEADHUNTER_MAX_DELAY", cfg.max_delay)
    cfg.max_concurrency = _env_int("LEADHUNTER_CONCURRENCY", cfg.max_concurrency)
    cfg.max_search_results = _env_int("LEADHUNTER_MAX_RESULTS", cfg.max_search_results)
    cfg.max_sites_per_run = _env_int("LEADHUNTER_MAX_SITES", cfg.max_sites_per_run)

    cfg.proxy_enabled = _env_bool("LEADHUNTER_PROXY_ENABLED", cfg.proxy_enabled)
    cfg.proxies = _env_list("LEADHUNTER_PROXIES", "")
    return cfg


def get_config() -> Config:
    """Build a Config resolved from environment variables at call time."""
    global _CONFIG
    cfg = Config()
    _resolve_paths(cfg)
    _resolve_env(cfg)
    _CONFIG = cfg
    return cfg
