"""
Phone number normalization using Google's `phonenumbers` library.

Converts local formats (Egypt: 010/011/012/015, 02 for Cairo/Giza landlines,
etc.) into E.164 international format and validates them.
"""
from __future__ import annotations

from typing import List, Optional

import phonenumbers
from phonenumbers import NumberParseException

from ..config import get_config
from .text import extract_phones_from_text


def _region() -> str:
    return get_config().default_phone_region


def normalize_phone(raw: Optional[str], region: Optional[str] = None) -> Optional[str]:
    """Parse a raw phone string and return E.164 format (or None if invalid)."""
    if not raw:
        return None
    region = region or _region()
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        num = phonenumbers.parse(raw, region)
        if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except NumberParseException:
        pass
    # Fallback: try without region hints
    try:
        num = phonenumbers.parse(raw, None)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except NumberParseException:
        pass
    return None


def format_phone_international(raw: Optional[str], region: Optional[str] = None) -> Optional[str]:
    """Pretty international format e.g. '+20 100 123 4567'."""
    if not raw:
        return None
    region = region or _region()
    try:
        num = phonenumbers.parse(raw, region)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except NumberParseException:
        pass
    return None


def is_valid_phone(raw: Optional[str], region: Optional[str] = None) -> bool:
    return normalize_phone(raw, region) is not None


def pick_best_phones(text_or_list, region: Optional[str] = None,
                     max_n: int = 5) -> List[str]:
    """Extract, normalize and dedup phone numbers from text or a list.

    Returns up to `max_n` E.164 numbers. Mobile numbers are preferred.
    """
    if isinstance(text_or_list, str):
        candidates = extract_phones_from_text(text_or_list)
    else:
        candidates = list(text_or_list)

    region = region or _region()
    seen, mobile, fixed = set(), [], []
    for c in candidates:
        normalized = normalize_phone(c, region)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            num = phonenumbers.parse(normalized, None)
            if phonenumbers.number_type(num) in (
                phonenumbers.PhoneNumberType.MOBILE,
                phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE):
                mobile.append(normalized)
            else:
                fixed.append(normalized)
        except NumberParseException:
            fixed.append(normalized)
    return (mobile + fixed)[:max_n]
