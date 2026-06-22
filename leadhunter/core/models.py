"""
The Lead entity — the single business record that flows through the platform.

A Lead is a plain dataclass so it's easy to serialize to SQLite, CSV, Excel,
and JSON. The field order in LEAD_FIELD_ORDER defines column order in exports.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# Canonical column order — used by exporters and the dashboard.
LEAD_FIELD_ORDER = [
    "company_name", "industry", "category", "website", "email", "phone",
    "whatsapp", "address", "city", "governorate", "country",
    "maps_link", "linkedin_url", "facebook_url", "instagram_url",
    "employees", "description", "contact_person", "source_url",
    "confidence_score", "confidence_tier",
    # internal tracking (kept but not exported by default)
    "dedup_key", "discovered_at", "updated_at", "lead_id",
]


@dataclass
class Lead:
    # Required-ish
    company_name: str = ""
    # Classification
    industry: str = ""
    category: str = ""
    # Web
    website: str = ""
    source_url: str = ""
    # Contact
    email: str = ""
    phone: str = ""
    whatsapp: str = ""
    contact_person: str = ""
    # Location
    address: str = ""
    city: str = ""
    governorate: str = ""
    country: str = ""
    maps_link: str = ""
    # Social
    linkedin_url: str = ""
    facebook_url: str = ""
    instagram_url: str = ""
    # Profile
    employees: str = ""
    description: str = ""
    # Quality
    confidence_score: int = 0
    confidence_tier: str = ""
    # Bookkeeping
    dedup_key: str = ""
    discovered_at: str = ""
    updated_at: str = ""
    lead_id: Optional[int] = None

    # ---- Convenience -----------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    def export_dict(self) -> dict:
        """Dict for CSV/Excel — drops internal keys."""
        d = self.to_dict()
        for k in ("lead_id", "dedup_key"):
            d.pop(k, None)
        return d

    def merge(self, other: "Lead") -> "Lead":
        """Fill missing fields from `other` (non-destructive merge)."""
        for f in LEAD_FIELD_ORDER:
            if f in ("lead_id", "discovered_at", "updated_at", "dedup_key"):
                continue
            mine = getattr(self, f, "")
            theirs = getattr(other, f, "")
            if not mine and theirs:
                setattr(self, f, theirs)
        return self


def lead_fields() -> list[str]:
    return list(LEAD_FIELD_ORDER)
