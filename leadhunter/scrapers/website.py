"""
Company-website scraper.

Given a website URL, fetch the homepage + a few likely contact/about pages,
then extract emails, phone numbers, social links, description, address,
employees and contact-person. Returns a Lead populated as fully as possible.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import get_config
from ..core.models import Lead
from ..utils.emailutil import classify_email
from ..utils.logger import get_logger
from ..utils.phone import pick_best_phones
from ..utils.scoring import compute_confidence
from ..utils.text import (
    clean_text, extract_emails_from_text, extract_social_links,
    guess_industry_from_keywords, looks_like_business_email, normalize_url,
)
from .base import BaseHTTPClient

log = get_logger(__name__)

# Common path stems that usually hold contact info / about content.
_CONTACT_PATHS = ["contact", "contact-us", "contacts", "about", "about-us",
                  "reach-us", "reach", "get-in-touch", "support"]
_ABOUT_PATHS = ["about", "about-us", "company", "who-we-are", "our-story",
                "our-team", "team", "profile"]

# Pattern used to detect WhatsApp click-to-chat links
_WHATSAPP_RE = re.compile(r"wa\.me/(\d+)|whatsapp\.com/(\d+)|api\.whatsapp\.com.*?phone=(\d+)",
                          re.IGNORECASE)
# Pull digits out of whatsapp URLs
_DIGITS_RE = re.compile(r"\d+")


class WebsiteScraper(BaseHTTPClient):
    def enrich(self, lead: Lead) -> Lead:
        """Fetch the website, extract contact details, update `lead` in place."""
        if not lead.website:
            return lead
        site = normalize_url(lead.website)
        if not site:
            return lead
        try:
            base = site
            pages: List[str] = [site]
            htmls: List[str] = []
            home = self.get_text(site)
            if not home:
                log.info("website unreachable: %s", site)
                return lead

            htmls.append(home)
            # Discover candidate contact/about pages from the homepage nav
            extra_paths = self._discover_pages(home, base)
            cfg = get_config()
            for path in extra_paths[: max(cfg.enrich_fetch_pages, 1)]:
                full = normalize_url(path, base)
                if not full or full == base:
                    continue
                txt = self.get_text(full)
                if txt:
                    htmls.append(txt)

            combined_html = "\n".join(htmls)
            # Plain text (strips tags) for description & email mining
            plain_texts = [self._html_to_text(h) for h in htmls]
            combined_text = "\n".join(plain_texts)

            self._populate(lead, base, combined_html, combined_text, home)
            lead.confidence_score = compute_confidence(lead)
            from ..utils.scoring import confidence_tier
            lead.confidence_tier = confidence_tier(lead.confidence_score)
            if lead.website and not lead.source_url:
                lead.source_url = base
        except Exception as e:
            log.warning("enrich failed for %s: %s", site, e)
        return lead

    # ------------------------------------------------------------------
    # Discovery & extraction
    # ------------------------------------------------------------------
    def _discover_pages(self, home_html: str, base: str) -> List[str]:
        soup = BeautifulSoup(home_html, "lxml")
        candidates: List[str] = []
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            low = href.lower()
            if any(p in low for p in _CONTACT_PATHS + _ABOUT_PATHS):
                full = normalize_url(href, base)
                if full and full not in candidates:
                    candidates.append(full)
        return candidates

    def _populate(self, lead: Lead, base: str, html: str, text: str, home_html: str) -> None:
        # --- Emails ---
        emails = extract_emails_from_text(html)
        emails = [e for e in emails if classify_email(e) != "invalid"]
        # Prefer business emails, then role, then personal
        emails.sort(key=lambda e: (
            0 if classify_email(e) == "business" else
            1 if classify_email(e) == "role" else 2))
        if emails and not lead.email:
            lead.email = emails[0]

        # --- Phones ---
        phones = pick_best_phones(text or html, max_n=5)
        if phones and not lead.phone:
            lead.phone = phones[0]
        # WhatsApp: scan raw HTML for wa.me links
        if not lead.whatsapp:
            wa = self._extract_whatsapp(html)
            if wa:
                lead.whatsapp = wa

        # --- Social ---
        social = extract_social_links(html, base)
        if not lead.linkedin_url and social.get("linkedin"):
            lead.linkedin_url = social["linkedin"][0]
        if not lead.facebook_url and social.get("facebook"):
            lead.facebook_url = social["facebook"][0]
        if not lead.instagram_url and social.get("instagram"):
            lead.instagram_url = social["instagram"][0]

        # --- Company name (from <title> / og:site_name / <h1>) ---
        if not lead.company_name:
            lead.company_name = self._extract_company_name(home_html, base)

        # --- Description ---
        if not lead.description:
            lead.description = self._extract_description(home_html, text)

        # --- Address ---
        if not lead.address:
            lead.address = self._extract_address(html, text)

        # --- Industry / category ---
        if not lead.industry:
            blob = " ".join(filter(None, [lead.company_name, lead.description,
                                          lead.category, lead.address]))
            guess = guess_industry_from_keywords(blob)
            if guess:
                lead.industry = guess

        # --- Employees ---
        if not lead.employees:
            lead.employees = self._extract_employees(text)

        # --- Contact person ---
        if not lead.contact_person:
            lead.contact_person = self._extract_contact_person(home_html, text)

    # ------------------------------------------------------------------
    @staticmethod
    def _html_to_text(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return clean_text(soup.get_text(" ", strip=True))

    @staticmethod
    def _extract_company_name(home_html: str, base: str) -> str:
        soup = BeautifulSoup(home_html, "lxml")
        # og:site_name
        meta = soup.find("meta", attrs={"property": "og:site_name"})
        if meta and meta.get("content"):
            return clean_text(meta["content"])
        # title tag
        if soup.title and soup.title.string:
            title = clean_text(soup.title.string)
            # Strip suffixes like " - Home" / " | Official Site"
            for sep in [" | ", " - ", " — ", " :: "]:
                if sep in title:
                    title = title.split(sep)[0].strip()
            if title:
                return title[:120]
        # <h1>
        h1 = soup.find("h1")
        if h1:
            return clean_text(h1.get_text())[:120]
        # Fallback: domain
        return (urlparse(base).netloc or "").replace("www.", "").split(".")[0].title()

    @staticmethod
    def _extract_description(home_html: str, text: str) -> str:
        soup = BeautifulSoup(home_html, "lxml")
        for attr in (("name", "description"), ("property", "og:description")):
            meta = soup.find("meta", attrs={attr[0]: attr[1]})
            if meta and meta.get("content"):
                desc = clean_text(meta["content"])
                if len(desc) >= 30:
                    return desc[:500]
        # Fallback: first long-enough paragraph of body text
        if text:
            for chunk in text.split(". "):
                c = clean_text(chunk)
                if len(c) >= 60 and not c.lower().startswith(("cookie", "we use", "skip to")):
                    return c[:500]
        return ""

    @staticmethod
    def _extract_address(html: str, text: str) -> str:
        # Look for schema.org PostalAddress markup
        soup = BeautifulSoup(html, "lxml")
        for sel in ("[itemtype*='PostalAddress']",
                    "[itemtype*='LocalBusiness']",
                    "address",
                    ".address", "#address"):
            node = soup.select_one(sel)
            if node:
                addr = clean_text(node.get_text(" ", strip=True))
                if addr and len(addr) < 300:
                    return addr
        # Heuristic: lines with address keywords
        for line in (text or "").split("\n"):
            low = line.lower()
            if any(k in low for k in ("street", "st.", "road", "rd.", "avenue",
                                       "district", "building", "floor", "p.o.",
                                       "po box")) and 15 <= len(line) <= 200:
                return clean_text(line)
        return ""

    @staticmethod
    def _extract_whatsapp(html: str) -> str:
        m = _WHATSAPP_RE.search(html or "")
        if not m:
            return ""
        digits = "".join(g for g in m.groups() if g)
        digits = _DIGITS_RE.sub("", digits) or ""
        if not digits:
            # try api.whatsapp.com/?phone=
            phone_m = re.search(r"phone=(\d+)", html or "")
            digits = phone_m.group(1) if phone_m else ""
        if digits and len(digits) >= 8:
            from ..utils.phone import normalize_phone
            return normalize_phone("+" + digits) or ("+" + digits)
        return ""

    @staticmethod
    def _extract_employees(text: str) -> str:
        m = re.search(r"(\d{1,5})\s*[-+–]?\s*(?:employees|staff|workers|people)",
                      text or "", re.IGNORECASE)
        if m:
            n = m.group(1)
            val = int(n)
            if val < 10000:
                return f"{val} employees"
        m2 = re.search(r"(?:team of|over|more than)\s+(\d{1,5})\s+(?:employees|staff|professionals)",
                       text or "", re.IGNORECASE)
        if m2:
            return f"{m2.group(1)}+ employees"
        return ""

    @staticmethod
    def _extract_contact_person(home_html: str, text: str) -> str:
        soup = BeautifulSoup(home_html, "lxml")
        # Common patterns: "Contact: John Doe", "Sales Manager - Jane Smith"
        for label in ("Contact Person", "Contact", "Sales Manager",
                      "General Manager", "Owner", "CEO", "Marketing Manager"):
            rx = re.compile(re.escape(label) + r"\s*[:\-]\s*([A-Z][a-zA-Z.'\- ]{3,40})")
            m = rx.search(text or "")
            if m:
                name = clean_text(m.group(1))
                if name and len(name) < 60:
                    return name
        return ""
