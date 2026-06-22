"""
Email validation & classification.

Uses a robust RFC-ish regex for syntax, plus domain-level heuristics for
freemail / disposable / role-based classification. Optional MX-check is
provided but disabled by default to avoid network cost during scraping.
"""
from __future__ import annotations

import re
from typing import Optional

from .text import _PERSONAL_FREE_DOMAINS, _ROLE_LOCALPARTS

# Pragmatic email regex — good enough for scraping.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}"
    r"[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

_DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com",
    "tempmail.com", "throwawaymail.com", "yopmail.com",
    "getnada.com", "trashmail.com", "sharklasers.com",
}


def is_valid_email(email: Optional[str]) -> bool:
    """Syntactic validity check."""
    if not email:
        return False
    email = email.strip().lower().rstrip(".")
    if not _EMAIL_RE.match(email):
        return False
    local, _, domain = email.partition("@")
    if not local or not domain:
        return False
    if email.count("@") != 1:
        return False
    if len(email) > 254:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    return True


def classify_email(email: Optional[str]) -> str:
    """Return one of: 'business', 'role', 'personal', 'disposable', 'invalid'."""
    if not is_valid_email(email):
        return "invalid"
    email = email.strip().lower()
    local, _, domain = email.partition("@")
    if domain in _DISPOSABLE_DOMAINS:
        return "disposable"
    if local in _ROLE_LOCALPARTS:
        return "role"
    if domain in _PERSONAL_FREE_DOMAINS:
        return "personal"
    return "business"
