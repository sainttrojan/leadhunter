"""
Lead confidence scoring.

The score is a weighted 0–100 grade computed from the presence of high-
value fields. Weights are configurable in `config.Config` and default to:
  website 25, email 25, phone 20, social 15, description 15.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..config import get_config

if TYPE_CHECKING:
    from ..core.models import Lead


def compute_confidence(lead: "Lead") -> int:
    """Return a 0–100 integer confidence score for a Lead."""
    cfg = get_config()
    score = 0

    if lead.website and lead.website.strip():
        score += cfg.score_website
    if lead.email and lead.email.strip():
        score += cfg.score_email
    if lead.phone and lead.phone.strip():
        score += cfg.score_phone

    social_present = any([
        lead.linkedin_url, lead.facebook_url, lead.instagram_url])
    if social_present:
        score += cfg.score_social

    if lead.description and len(lead.description.strip()) >= 40:
        score += cfg.score_description

    # Small bonus: contact person adds value
    if getattr(lead, "contact_person", None) and lead.contact_person.strip():
        score = min(100, score + 2)

    return max(0, min(100, int(score)))


def confidence_tier(score: int) -> str:
    """Map a numeric score to a human tier label."""
    if score >= 80:
        return "A (High)"
    if score >= 60:
        return "B (Medium)"
    if score >= 40:
        return "C (Low)"
    return "D (Minimal)"
