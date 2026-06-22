"""
Scrapers layer.

BaseHTTPClient   — robust HTTP with retries, backoff, rate limiting, proxy rotation
SearchEngineScraper — DuckDuckGo + Google + Bing HTML result scraping
WebsiteScraper   — parse company sites for contact / social / about info
OverpassScraper  — OpenStreetMap / Nominatim (Google Maps alternative)
DirectoryScraper — aggregator of public business directories
"""
from .base import BaseHTTPClient
from .search_engines import SearchEngineScraper
from .website import WebsiteScraper
from .overpass import OverpassScraper
from .directories import DirectoryScraper

__all__ = [
    "BaseHTTPClient",
    "SearchEngineScraper",
    "WebsiteScraper",
    "OverpassScraper",
    "DirectoryScraper",
]
