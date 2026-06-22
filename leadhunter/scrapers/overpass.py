"""
OpenStreetMap scrapers — a free, key-less Google Maps alternative.

Two endpoints are used:
  * Nominatim  — geocode a place name (e.g. "Asyut, Egypt") -> lat/lon
  * Overpass   — query OSM for businesses (shop/office/amenity/healthcare)
                 inside an area or radius

Multiple Overpass mirror servers are tried in rotation for resilience.
Results are normalized into Lead objects.
"""
from __future__ import annotations

import time
from typing import List, Optional
from urllib.parse import quote_plus

from ..config import get_config
from ..core.models import Lead
from ..utils.logger import get_logger
from ..utils.phone import normalize_phone
from ..utils.scoring import compute_confidence, confidence_tier
from ..utils.text import clean_text, normalize_url
from .base import BaseHTTPClient

log = get_logger(__name__)

# OSM tag -> our Industry taxonomy
_INDUSTRY_MAP = {
    "dentist": "Dental & Healthcare", "doctors": "Dental & Healthcare",
    "clinic": "Dental & Healthcare", "hospital": "Dental & Healthcare",
    "pharmacy": "Dental & Healthcare", "optician": "Dental & Healthcare",
    "car": "Automotive", "car_repair": "Automotive", "car_rental": "Automotive",
    "motorcycle_repair": "Automotive", "tyres": "Automotive",
    "it": "Software & IT", "computer": "Software & IT", "telecommunication": "Software & IT",
    "company": "Construction & Real Estate", "estate_agent": "Construction & Real Estate",
    "architect": "Construction & Real Estate", "engineering": "Construction & Real Estate",
    "lawyer": "Legal Services", "notary": "Legal Services",
    "accountant": "Finance & Banking", "insurance": "Finance & Banking",
    "financial": "Finance & Banking",
    "restaurant": "Food & Beverage", "cafe": "Food & Beverage",
    "fast_food": "Food & Beverage", "bakery": "Food & Beverage", "bar": "Food & Beverage",
    "supermarket": "Retail & E-commerce", "clothes": "Retail & E-commerce",
    "convenience": "Retail & E-commerce", "mall": "Retail & E-commerce",
    "beauty": "Beauty & Wellness", "hairdresser": "Beauty & Wellness",
    "spa": "Beauty & Wellness", "fitness_centre": "Beauty & Wellness",
    "school": "Education & Training", "college": "Education & Training",
    "university": "Education & Training", "training": "Education & Training",
    "kindergarten": "Education & Training",
    "storage": "Logistics & Transport", "courier": "Logistics & Transport",
    "fuel": "Logistics & Transport",
}

# Maps our friendly category to an OSM node filter
_KEYWORD_FILTERS = {
    "dental": ["healthcare", "amenity"],
    "dentist": ["healthcare", "amenity"],
    "clinic": ["amenity"],
    "hospital": ["amenity"],
    "car": ["shop", "amenity"],
    "automotive": ["shop", "amenity"],
    "software": ["office"],
    "it": ["office"],
    "construction": ["office", "craft"],
    "logistics": ["office", "man_made"],
    "restaurant": ["amenity"],
    "lawyer": ["office"],
    "real estate": ["office"],
    "marketing": ["office"],
    "school": ["amenity"],
    "academy": ["amenity"],
    "beauty": ["shop"],
    "salon": ["shop"],
}

# Multiple Overpass mirrors for resilience — rotated on failure.
_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


class OverpassScraper(BaseHTTPClient):
    NOMINATIM = "https://nominatim.openstreetmap.org/search"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mirror_index = 0

    def search_area(self, *, query: str, place: str,
                    country: str = "Egypt", limit: int = 60,
                    radius_km: Optional[int] = None) -> List[Lead]:
        """Find businesses around `place`.

        Steps:
          1. Geocode `place, country` to lat/lon.
          2. Build an Overpass QL query.
          3. Parse OSM elements into Lead records.
        """
        coords = self._geocode(f"{place}, {country}")
        if not coords:
            log.warning("geocode failed for '%s, %s'", place, country)
            return []
        lat, lon, area_name = coords

        around = (radius_km or 0) * 1000 or 8000  # default 8km
        osm_query = self._build_query(lat, lon, around, query)
        leads = self._run_overpass(osm_query, place, country, limit)
        log.info("overpass '%s' near %s -> %d leads", query, place, len(leads))
        return leads

    # ------------------------------------------------------------------
    def _geocode(self, q: str):
        url = f"{self.NOMINATIM}?q={quote_plus(q)}&format=json&limit=1&addressdetails=1"
        data = self.get_json(url, extra_headers={"Referer": "leadhunter"},
                             timeout=10)
        if not data:
            return None
        hit = data[0]
        try:
            addr = hit.get("address", {})
            name = (addr.get("city") or addr.get("town")
                    or addr.get("village") or addr.get("state")
                    or addr.get("county") or "").strip()
            return (float(hit["lat"]), float(hit["lon"]), name or q)
        except (KeyError, ValueError, TypeError):
            return None

    def _build_query(self, lat: float, lon: float, around_m: int, keyword: str) -> str:
        kw = keyword.lower().strip()
        buckets = _KEYWORD_FILTERS.get(kw, ["shop", "office", "amenity", "healthcare"])
        parts = []
        for tag_key in set(buckets):
            parts.append(
                f'node(around:{around_m},{lat},{lon})["name"][~"{tag_key}"~".*"];')
            parts.append(
                f'way(around:{around_m},{lat},{lon})["name"][~"{tag_key}"~".*"];')
        body = "\n".join(parts)
        return f"[out:json][timeout:20];(\n{body}\n);out center tags 300;"

    def _run_overpass(self, query: str, place: str, country: str,
                      limit: int) -> List[Lead]:
        """Try each Overpass mirror in rotation until one succeeds."""
        # Try up to 3 different mirrors
        for attempt in range(min(3, len(_OVERPASS_MIRRORS))):
            url = _OVERPASS_MIRRORS[self._mirror_index % len(_OVERPASS_MIRRORS)]
            self._mirror_index += 1
            log.info("trying Overpass mirror: %s", url)
            data = self._post_overpass(url, query)
            if data and "elements" in data:
                return self._parse_elements(data, place, country, limit)
            log.warning("mirror %s returned no data, trying next", url)
        return []

    def _post_overpass(self, url: str, query: str):
        """POST the Overpass QL query to a single mirror."""
        host = url.split("//")[1].split("/")[0].split(":")[0]
        self._respect_rate_limit(host)
        try:
            resp = self.session.post(
                url, data={"data": query},
                timeout=15,  # tight timeout for Overpass
                headers=dict(self.session.headers),
                proxies=self._next_proxy())
            if resp.status_code == 429:
                log.warning("Overpass rate-limited on %s, backing off 30s", host)
                time.sleep(30)
                resp = self.session.post(
                    url, data={"data": query},
                    timeout=15,
                    headers=dict(self.session.headers),
                    proxies=self._next_proxy())
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning("Overpass POST to %s failed: %s", host, e)
            return None

    def _parse_elements(self, data: dict, place: str, country: str,
                       limit: int) -> List[Lead]:
        leads: List[Lead] = []
        seen_names: set = set()
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            name = clean_text(tags.get("name"))
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())
            lead = self._element_to_lead(el, tags, place, country)
            if lead:
                leads.append(lead)
            if len(leads) >= limit:
                break
        return leads

    def _element_to_lead(self, el: dict, tags: dict, place: str, country: str) -> Optional[Lead]:
        name = clean_text(tags.get("name"))
        if not name:
            return None
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")

        primary_value = ""
        for k in ("shop", "amenity", "office", "healthcare", "craft", "tourism"):
            if k in tags:
                primary_value = f"{k}={tags[k]}"
                break
        industry = self._industry_for(tags)
        category = clean_text(tags.get("category") or primary_value.replace("_", " "))

        website = normalize_url(tags.get("website") or tags.get("contact:website")
                                or tags.get("url"))
        email = (tags.get("email") or tags.get("contact:email") or "").strip().lower()
        phone_raw = (tags.get("phone") or tags.get("contact:phone")
                     or tags.get("contact:mobile") or "")
        phone = normalize_phone(phone_raw) if phone_raw else ""
        whatsapp_raw = tags.get("contact:whatsapp") or ""
        whatsapp = normalize_phone("+" + whatsapp_raw) if whatsapp_raw else ""

        addr_parts = [tags.get("addr:housenumber"), tags.get("addr:street"),
                      tags.get("addr:district"), tags.get("addr:neighbourhood")]
        address = clean_text(", ".join(p for p in addr_parts if p))
        city = clean_text(tags.get("addr:city") or place)
        governorate = clean_text(tags.get("addr:state") or "")

        maps_link = ""
        if lat and lon:
            maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

        facebook = normalize_url(tags.get("contact:facebook"))
        instagram = normalize_url(tags.get("contact:instagram"))
        linkedin = normalize_url(tags.get("contact:linkedin"))

        description_parts = []
        if tags.get("description"):
            description_parts.append(tags["description"])
        for k in ("shop", "amenity", "office", "healthcare"):
            if k in tags:
                description_parts.append(f"{k.replace('_', ' ').title()}: {tags[k].replace('_',' ')}")
        description = clean_text(" — ".join(description_parts))[:500]

        lead = Lead(
            company_name=name, industry=industry, category=category,
            website=website, email=email, phone=phone, whatsapp=whatsapp,
            address=address, city=city, governorate=governorate, country=country,
            maps_link=maps_link, facebook_url=facebook, instagram_url=instagram,
            linkedin_url=linkedin, description=description,
            source_url="https://www.openstreetmap.org",
        )
        lead.confidence_score = compute_confidence(lead)
        lead.confidence_tier = confidence_tier(lead.confidence_score)
        return lead

    # ------------------------------------------------------------------
    @staticmethod
    def _industry_for(tags: dict) -> str:
        for k in ("shop", "amenity", "office", "healthcare", "craft"):
            v = (tags.get(k) or "").lower()
            if v in _INDUSTRY_MAP:
                return _INDUSTRY_MAP[v]
        from ..utils.text import guess_industry_from_keywords
        blob = " ".join([tags.get("description", ""), tags.get("name", "")])
        return guess_industry_from_keywords(blob) or "Other"
