"""
LeadHunter — A modular business lead generation platform.

Public modules
--------------
core.models      — Lead dataclass
core.database    — SQLite persistence (DAL)
core.exporters   — CSV / Excel export
utils.*          — logger, phone, email, scoring, text helpers
scrapers.*       — search engines, website parser, OpenStreetMap, directories
pipeline         — orchestrates discovery + enrichment + scoring
reporting        — daily new/updated/missing-info reports
scheduler        — APScheduler-based scan scheduling
app              — Streamlit dashboard
cli              — command line interface
"""
from __future__ import annotations

__version__ = "1.0.0"
__author__ = "LeadHunter"

# Re-export a single Config instance for convenience. Modules that need
# live overrides should call config.get_config() themselves.
from .config import Config, get_config

__all__ = ["Config", "get_config", "__version__"]
