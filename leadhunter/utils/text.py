"""
Text & extraction helpers — pure functions, no network.
Used by the website parser and directory scrapers.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional
from urllib.parse import urlparse, urljoin

# ---------------------------------------------------------------------------
# Common patterns
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
# International-format phone (with optional country code) — lenient.
_PHONE_RE = re.compile(
    r"(?:\+?\d[\d\-\s().]{7,}\d)")
# Tightened phone: groups of digits with separators
_PHONE_CLEAN_RE = re.compile(r"[^\d+]")

# Domains that are clearly generic/non-business and should be excluded as
# "business emails". Personal freemail providers are excluded too.
_PERSONAL_FREE_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "icloud.com", "aol.com", "protonmail.com", "mail.com", "zoho.com",
    "msn.com", "yandex.com", "gmx.com",
}
_ROLE_LOCALPARTS = {
    "info", "contact", "sales", "support", "admin", "marketing",
    "hello", "office", "inquiries", "enquiries", "booking", "bookings",
    "reservations", "service", "hr", "careers", "jobs", "press",
    "media", "team", "general", "mail", "business", "customer",
    "customerservice",
}

_SOCIAL_HOSTS = {
    "linkedin": ("linkedin.com",),
    "facebook": ("facebook.com", "fb.com", "m.facebook.com"),
    "instagram": ("instagram.com",),
    "twitter": ("twitter.com", "x.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com",),
}

# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")


def clean_text(value: Optional[str]) -> str:
    """Trim, collapse whitespace, strip NULs and control chars."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\xa0", " ").replace("\u200f", "").replace("\u200e", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def normalize_url(url: Optional[str], base: Optional[str] = None) -> str:
    """Normalize a URL — add scheme, drop tracking query params & fragments."""
    if not url:
        return ""
    url = clean_text(url)
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(base or "https://example.com", url)
    elif not _SCHEME_RE.match(url):
        if "@" in url.split("/")[0] or " " in url:
            return ""
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    # Lowercase host, strip 'www.', drop fragment, drop common tracking params
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    scheme = parsed.scheme.lower() or "https"
    # Trim tracking params
    keep_q = []
    if parsed.query:
        for pair in parsed.query.split("&"):
            k = pair.split("=", 1)[0].lower()
            if k in ("utm_source", "utm_medium", "utm_campaign", "utm_term",
                     "utm_content", "fbclid", "gclid", "mc_eid", "_hsenc"):
                continue
            keep_q.append(pair)
    query = "&".join(keep_q)
    return f"{scheme}://{host}{parsed.path.rstrip('/') or ''}" + (
        f"?{query}" if query else "")


def normalize_domain(url: Optional[str]) -> str:
    """Extract the bare registered domain (host without www.)."""
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
_MAILTO_RE = re.compile(r"mailto:([^?\"'\s>]+)", re.IGNORECASE)
_TEL_RE = re.compile(r"tel:([0-9+().\-\s]+)", re.IGNORECASE)


def extract_emails_from_text(text: str) -> List[str]:
    """Return unique, lowercased emails found in `text` (incl. mailto:)."""
    if not text:
        return []
    found: List[str] = []
    for m in _MAILTO_RE.finditer(text):
        found.append(m.group(1))
    for m in _EMAIL_RE.finditer(text):
        found.append(m.group(0))
    seen = set()
    result = []
    for e in found:
        e = e.strip().lower().rstrip(".")
        if "@" not in e:
            continue
        if e in seen:
            continue
        seen.add(e)
        result.append(e)
    return result


def extract_phones_from_text(text: str) -> List[str]:
    """Extract candidate phone numbers from raw text (incl. tel: links)."""
    if not text:
        return []
    candidates: List[str] = []
    for m in _TEL_RE.finditer(text):
        candidates.append(m.group(1))
    for m in _PHONE_RE.finditer(text):
        candidates.append(m.group(0))
    # Deduplicate on digit-sequence
    seen = set()
    out: List[str] = []
    for c in candidates:
        digits = _PHONE_CLEAN_RE.sub("", c)
        if len(digits) < 8 or len(digits) > 15:
            continue
        key = digits.lstrip("+")
        if key in seen:
            continue
        seen.add(key)
        out.append(c.strip())
    return out


def extract_social_links(text_or_html: str, base_url: Optional[str] = None) -> dict:
    """Find LinkedIn / Facebook / Instagram etc. links.

    Returns a dict keyed by platform -> list of normalized URLs.
    """
    out: dict = {k: [] for k in _SOCIAL_HOSTS}
    if not text_or_html:
        return out
    # Grab every http(s) link-like substring
    for m in re.finditer(
        r"https?://[a-zA-Z0-9_.\-:/?#\[\]@!$&'()*+,;=%~]+",
        text_or_html, re.IGNORECASE):
        link = normalize_url(m.group(0))
        if not link:
            continue
        try:
            host = urlparse(link).netloc.lower()
        except Exception:
            continue
        if host.startswith("www."):
            host = host[4:]
        for platform, hosts in _SOCIAL_HOSTS.items():
            if any(host == h or host.endswith("." + h) for h in hosts):
                if link not in out[platform]:
                    out[platform].append(link)
                break
    return out


# ---------------------------------------------------------------------------
# Industry guessing
# ---------------------------------------------------------------------------
_INDUSTRY_KEYWORDS = {
    "Dental & Healthcare": [
        "dental", "dentist", "clinic", "medical", "health", "hospital",
        "dentistry", "orthodont", "doctor", "dermatolog", "pharma", "dental clinic"],
    "Automotive": [
        "car", "auto", "automotive", "dealership", "vehicle", "motors",
        "tire", "tyre", "garage", "rent a car", "car rental"],
    "Software & IT": [
        "software", "it ", "technology", "tech ", "digital", "app development",
        "web development", "saas", "cloud", "cyber", "ai ", "data", "comput"],
    "Construction & Real Estate": [
        "construction", "contractor", "building", "real estate", "property",
        "engineering", "cement", "concrete", "architect"],
    "Logistics & Transport": [
        "logistics", "shipping", "freight", "transport", "courier", "cargo",
        "trucking", "warehouse", "delivery", "supply chain"],
    "Education & Training": [
        "school", "academy", "education", "training", "institute", "university",
        "learning", "course", "center"],
    "Food & Beverage": [
        "restaurant", "cafe", "coffee", "food", "bakery", "catering", "kitchen",
        "fast food", "kitchen"],
    "Retail & E-commerce": [
        "shop", "store", "retail", "mall", "fashion", "market", "ecommerce",
        "e-commerce"],
    "Finance & Banking": [
        "bank", "finance", "financial", "insurance", "investment", "accounting",
        "audit"],
    "Manufacturing": [
        "factory", "manufacturing", "industrial", "production", "plastic",
        "steel", "metal"],
    "Marketing & Media": [
        "marketing", "advertising", "media", "agency", "pr ", "production house"],
    "Beauty & Wellness": [
        "beauty", "spa", "salon", "gym", "fitness", "wellness", "barber"],
    "Legal Services": [
        "law", "lawyer", "legal", "attorney", "advocate"],
}


def guess_industry_from_keywords(text: str) -> Optional[str]:
    """Best-effort industry classification from a blob of text."""
    if not text:
        return None
    low = text.lower()
    best, best_score = None, 0
    for industry, kws in _INDUSTRY_KEYWORDS.items():
        score = sum(low.count(kw) for kw in kws)
        if score > best_score:
            best, best_score = industry, score
    return best if best_score > 0 else None


# ---------------------------------------------------------------------------
# Email quality
# ---------------------------------------------------------------------------
def looks_like_business_email(email: str) -> bool:
    """True if the email is on a non-freemail domain (i.e. a real company)."""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    return domain not in _PERSONAL_FREE_DOMAINS


def is_role_email(email: str) -> bool:
    """True if the local-part is a department/role mailbox (good for B2B)."""
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower().strip(".")
    return local in _ROLE_LOCALPARTS
