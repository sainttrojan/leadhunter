"""Utility helpers: logging, text normalization, phone/email validation, scoring."""
from .logger import get_logger
from .text import (
    clean_text, normalize_url, normalize_domain, extract_emails_from_text,
    extract_phones_from_text, extract_social_links, guess_industry_from_keywords,
    looks_like_business_email,
)
from .phone import normalize_phone, format_phone_international, is_valid_phone
from .emailutil import is_valid_email, classify_email
from .scoring import compute_confidence

__all__ = [
    "get_logger",
    "clean_text", "normalize_url", "normalize_domain",
    "extract_emails_from_text", "extract_phones_from_text",
    "extract_social_links", "guess_industry_from_keywords",
    "looks_like_business_email",
    "normalize_phone", "format_phone_international", "is_valid_phone",
    "is_valid_email", "classify_email",
    "compute_confidence",
]
