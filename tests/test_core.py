"""
Unit tests for LeadHunter — fast, offline, no network required.

Run with:  pytest  (from the project root)
"""
import os
import sys
import tempfile

# Make the package importable when pytest is invoked from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from leadhunter.core.models import Lead
from leadhunter.core.database import Database
from leadhunter.core.exporters import leads_to_dataframe, export_csv, export_excel
from leadhunter.utils.text import (
    clean_text, normalize_url, normalize_domain,
    extract_emails_from_text, extract_phones_from_text, extract_social_links,
    guess_industry_from_keywords, looks_like_business_email,
)
from leadhunter.utils.phone import normalize_phone, is_valid_phone, pick_best_phones
from leadhunter.utils.emailutil import is_valid_email, classify_email
from leadhunter.utils.scoring import compute_confidence, confidence_tier


# ---------------------------------------------------------------------------
# text utils
# ---------------------------------------------------------------------------
class TestTextUtils:
    def test_clean_text_collapses_whitespace(self):
        assert clean_text("  Hello   world  \n") == "Hello world"

    def test_clean_text_strips_control_chars(self):
        assert clean_text("a\x00b\x07c") == "abc"

    def test_normalize_url_adds_scheme(self):
        assert normalize_url("example.com") == "https://example.com"

    def test_normalize_url_strips_www_and_tracking(self):
        u = normalize_url("https://www.example.com/page?utm_source=x&id=5")
        assert u == "https://example.com/page?id=5"

    def test_normalize_url_handles_protocol_relative(self):
        assert normalize_url("//ex.com").startswith("https://ex.com")

    def test_normalize_url_rejects_garbage(self):
        assert normalize_url("not a url @ all") == ""
        assert normalize_url("") == ""

    def test_normalize_domain(self):
        assert normalize_domain("https://www.example.com/x") == "example.com"
        assert normalize_domain("example.com") == "example.com"

    def test_extract_emails(self):
        text = "Contact: sales@acme.com or MAILTO:Info@Acme.com"
        emails = extract_emails_from_text(text)
        assert "sales@acme.com" in emails
        assert "info@acme.com" in emails
        assert len(emails) == 2

    def test_extract_emails_dedup(self):
        emails = extract_emails_from_text("a@x.com a@x.com")
        assert emails == ["a@x.com"]

    def test_extract_phones(self):
        text = "Call us at +20 100 123 4567 or tel:+201001234567"
        phones = extract_phones_from_text(text)
        assert len(phones) >= 1

    def test_extract_social_links(self):
        html = ('Visit <a href="https://www.facebook.com/acme">fb</a> '
                'and https://www.linkedin.com/company/acme plus '
                'https://instagram.com/acme')
        social = extract_social_links(html)
        assert any("facebook.com/acme" in u for u in social["facebook"])
        assert any("linkedin.com/company/acme" in u for u in social["linkedin"])
        assert any("instagram.com/acme" in u for u in social["instagram"])

    def test_guess_industry(self):
        assert guess_industry_from_keywords("Family dental clinic and orthodontics") == "Dental & Healthcare"
        assert guess_industry_from_keywords("Buy used cars and auto repair") == "Automotive"
        assert guess_industry_from_keywords("Software development and web design") in ("Software & IT",)
        assert guess_industry_from_keywords("nothing relevant here") is None

    def test_business_vs_freemail(self):
        assert looks_like_business_email("sales@acme.com") is True
        assert looks_like_business_email("john@gmail.com") is False


# ---------------------------------------------------------------------------
# phone utils
# ---------------------------------------------------------------------------
class TestPhoneUtils:
    def test_normalize_eg_mobile(self):
        assert normalize_phone("0100 123 4567", "EG") == "+201001234567"
        assert normalize_phone("1001234567", "EG") == "+201001234567"

    def test_normalize_international(self):
        assert normalize_phone("+20 100 123 4567") == "+201001234567"

    def test_invalid_phone(self):
        assert normalize_phone("abc") is None
        assert normalize_phone("123") is None

    def test_is_valid(self):
        assert is_valid_phone("+201001234567") is True
        assert is_valid_phone("not a number") is False

    def test_pick_best_phones_prefers_mobile(self):
        text = "Office +20 2 25760000, mobile +20 100 123 4567"
        best = pick_best_phones(text)
        assert "+201001234567" in best


# ---------------------------------------------------------------------------
# email utils
# ---------------------------------------------------------------------------
class TestEmailUtils:
    def test_valid_emails(self):
        for e in ("sales@acme.com", "john.doe+filter@example.co.uk",
                  "info@sub.domain.example.org"):
            assert is_valid_email(e), e

    def test_invalid_emails(self):
        for e in ("", "noatsign", "a@b", "@x.com", "a@.com", "a b@x.com"):
            assert not is_valid_email(e), e

    def test_classify(self):
        # sales@/info@ are department mailboxes -> 'role'
        assert classify_email("sales@acme.com") == "role"
        assert classify_email("info@acme.com") == "role"
        # a person's name on a company domain -> 'business'
        assert classify_email("john.doe@acme.com") == "business"
        assert classify_email("contact@startup.io") == "role"
        assert classify_email("john@gmail.com") == "personal"
        assert classify_email("x@mailinator.com") == "disposable"
        assert classify_email("not an email") == "invalid"


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
class TestScoring:
    def test_empty_lead_scores_zero(self):
        lead = Lead(company_name="X")
        assert compute_confidence(lead) == 0

    def test_full_lead_scores_100(self):
        lead = Lead(
            website="https://acme.com", email="sales@acme.com",
            phone="+201001234567", linkedin_url="https://linkedin.com/x",
            description="A long enough company description to score full marks.")
        assert compute_confidence(lead) == 100

    def test_partial_score(self):
        lead = Lead(website="https://acme.com", email="sales@acme.com")
        # website (25) + email (25) = 50
        assert compute_confidence(lead) == 50

    def test_tiers(self):
        assert confidence_tier(90) == "A (High)"
        assert confidence_tier(65) == "B (Medium)"
        assert confidence_tier(45) == "C (Low)"
        assert confidence_tier(10) == "D (Minimal)"


# ---------------------------------------------------------------------------
# database (uses a temp DB so tests are hermetic)
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_leads.db")
    return Database(db_path=db_path)


class TestDatabase:
    def test_insert_and_count(self, tmp_db):
        lead = Lead(company_name="Acme Inc.", website="https://acme.com",
                    email="sales@acme.com", country="Egypt",
                    confidence_score=70)
        stored, action = tmp_db.upsert_lead(lead)
        assert action == "inserted"
        assert tmp_db.count() == 1
        assert stored.lead_id is not None

    def test_dedup_by_domain(self, tmp_db):
        l1 = Lead(company_name="Acme", website="https://acme.com",
                  email="a@acme.com")
        l2 = Lead(company_name="Acme Co", website="https://www.acme.com/",
                  phone="+201001234567")
        tmp_db.upsert_lead(l1)
        stored, action = tmp_db.upsert_lead(l2)
        assert action in ("updated", "unchanged")
        assert tmp_db.count() == 1
        # Phone should have been backfilled
        fetched = tmp_db.get_lead(stored.lead_id)
        assert fetched.phone == "+201001234567"

    def test_search_filters(self, tmp_db):
        tmp_db.upsert_lead(Lead(company_name="A", industry="Dental & Healthcare",
                                city="Asyut", country="Egypt", website="https://a.com"))
        tmp_db.upsert_lead(Lead(company_name="B", industry="Automotive",
                                city="Cairo", country="Egypt", website="https://b.com"))
        dental = tmp_db.search(industry="Dental & Healthcare")
        assert len(dental) == 1 and dental[0].company_name == "A"
        cairo = tmp_db.search(city="Cairo")
        assert len(cairo) == 1 and cairo[0].company_name == "B"
        egypt = tmp_db.search(country="Egypt")
        assert len(egypt) == 2

    def test_stats_shape(self, tmp_db):
        tmp_db.upsert_lead(Lead(company_name="A", industry="Automotive",
                                city="Cairo", country="Egypt",
                                website="https://a.com",
                                confidence_score=60, confidence_tier="B (Medium)"))
        stats = tmp_db.stats()
        assert stats["total"] == 1
        assert "Automotive" in stats["by_industry"]
        assert "Cairo" in stats["by_city"]
        assert stats["by_country"].get("Egypt") == 1

    def test_counts_since(self, tmp_db):
        tmp_db.upsert_lead(Lead(company_name="A", website="https://a.com"))
        c = tmp_db.counts_since("2000-01-01")
        assert c["total"] == 1 and c["new"] == 1


# ---------------------------------------------------------------------------
# exporters
# ---------------------------------------------------------------------------
class TestExporters:
    def test_dataframe_columns(self):
        leads = [Lead(company_name="A", website="https://a.com")]
        df = leads_to_dataframe(leads)
        assert "company_name" in df.columns
        assert "lead_id" not in df.columns
        assert len(df) == 1

    def test_csv_export(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LEADHUNTER_EXPORT_DIR", str(tmp_path))
        from leadhunter.config import get_config
        # Force config refresh
        get_config()
        leads = [Lead(company_name="Acme", website="https://acme.com",
                      email="sales@acme.com")]
        path = export_csv(leads, filename="t.csv")
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        assert "Acme" in content and "sales@acme.com" in content

    def test_excel_export(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LEADHUNTER_EXPORT_DIR", str(tmp_path))
        from leadhunter.config import get_config
        get_config()
        leads = [Lead(company_name="Acme", website="https://acme.com")]
        path = export_excel(leads, filename="t.xlsx")
        assert os.path.exists(path) and path.endswith(".xlsx")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
