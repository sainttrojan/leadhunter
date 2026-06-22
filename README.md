# 🎯 LeadHunter — Business Lead Generation Platform

A complete, production-ready lead generation platform that discovers businesses
in a specific **country / governorate / city / district / industry**, extracts
their contact details, scores lead quality, and stores them in a queryable
database with a modern dashboard.

Built to run continuously on a **Windows VPS** and build a high-quality
business lead database 24/7.

---

## ✨ Features

| Area | What you get |
|------|--------------|
| **Search** | By industry, keyword, governorate, city, or radius |
| **Discovery** | OpenStreetMap (geo), DuckDuckGo + Google (web), YellowPages/HotFrog (directories) |
| **Enrichment** | Auto-visits each company website → emails, phones, WhatsApp, social, address, description, employees |
| **Normalization** | E.164 phone format, validated emails, dedup by domain |
| **Quality** | 0–100 confidence score (website/email/phone/social/description) with A–D tiers |
| **Storage** | SQLite (default) + CSV + Excel exports |
| **Dashboard** | Streamlit UI with KPIs, charts, filters, exports, scheduling |
| **Reports** | Daily / weekly / monthly: new leads, updated leads, missing-contact list |
| **Scheduling** | APScheduler presets: daily (02:00), weekly (Mon 03:00), monthly (1st 04:00) |
| **Production** | Structured logging, retries + exponential backoff, per-host rate limiting, rotating proxies |

---

## 📁 Project structure

```
leedhunter/
├── leadhunter/                 ← the platform package
│   ├── __init__.py
│   ├── __main__.py             ← `python -m leadhunter …`
│   ├── config.py               ← all tunables + env overrides
│   ├── cli.py                  ← command-line interface
│   ├── app.py                  ← Streamlit dashboard
│   ├── pipeline.py             ← discover → enrich → dedup → store
│   ├── reporting.py            ← daily/weekly/monthly reports
│   ├── scheduler.py            ← APScheduler + saved searches
│   ├── core/
│   │   ├── models.py           ← Lead dataclass
│   │   ├── database.py         ← SQLite DAL (upsert/search/stats)
│   │   └── exporters.py        ← CSV / Excel
│   ├── scrapers/
│   │   ├── base.py             ← HTTP client (retry/rate-limit/proxy)
│   │   ├── search_engines.py   ← DuckDuckGo + Google
│   │   ├── website.py          ← company-site contact parser
│   │   ├── overpass.py         ← OpenStreetMap (Maps alternative)
│   │   └── directories.py      ← YellowPages / HotFrog
│   └── utils/
│       ├── logger.py
│       ├── text.py             ← cleaning, extraction, industry guess
│       ├── phone.py            ← E.164 normalization (phonenumbers)
│       ├── emailutil.py        ← validation + classification
│       └── scoring.py          ← confidence score
├── tests/test_core.py          ← offline unit tests
├── requirements.txt
├── setup.bat                   ← one-time install
├── run_dashboard.bat           ← Streamlit UI
├── run_scan.bat                ← one-shot scan
├── run_scheduler.bat           ← 24/7 scheduler
└── README.md
```

`data/`, `exports/`, `reports/`, `logs/` are created automatically.

---

## 🚀 Quick start

### 1. Install

```bat
setup.bat
```

Or manually:

```bat
python -m pip install -r requirements.txt
```

### 2. Run a search (CLI)

```bat
python -m leadhunter scan --query "Dental Clinics" --city Asyut --country Egypt --export csv
```

Or use the launcher:

```bat
run_scan.bat --query "Car Dealerships" --city Cairo
```

### 3. Open the dashboard

```bat
run_dashboard.bat
```

Then visit **http://localhost:8501**.

### 4. Run continuously on a VPS

```bat
run_scheduler.bat
```

This schedules **daily + weekly + monthly** scans of every saved search.

---

## 🔍 Search examples

The platform ships with these as default recurring searches in `searches.json`:

| Query | City | Country |
|-------|------|---------|
| Dental Clinics | Asyut | Egypt |
| Car Dealerships | Cairo | Egypt |
| Software Companies | Alexandria | Egypt |
| Construction Companies | Giza | Egypt |
| Logistics Companies | *(all)* | Egypt |

Edit them from the dashboard's **⏰ Schedule → Recurring searches** table.

---

## 🧮 Lead fields captured

`company_name`, `industry`, `category`, `website`, `email`, `phone`,
`whatsapp`, `address`, `city`, `governorate`, `country`, `maps_link`,
`linkedin_url`, `facebook_url`, `instagram_url`, `employees`,
`description`, `contact_person`, `source_url`, `confidence_score`,
`confidence_tier`.

---

## 🎯 Confidence score

The 0–100 score is the weighted sum of:

| Signal | Weight |
|--------|--------|
| Website exists | 25 |
| Email exists | 25 |
| Phone exists | 20 |
| Social media exists | 15 |
| Description (≥40 chars) | 15 |
| *(bonus)* Contact person | +2 |

Tiers: **A ≥80** · **B ≥60** · **C ≥40** · **D <40**.

---

## ⚙️ Configuration

Everything is configurable via environment variables (see `leadhunter/config.py`).
The most useful ones:

| Env var | Default | Purpose |
|---------|---------|---------|
| `LEADHUNTER_COUNTRY` | `Egypt` | Default search country |
| `LEADHUNTER_PHONE_REGION` | `EG` | Phone normalization region |
| `LEADHUNTER_MIN_DELAY` / `MAX_DELAY` | `1.0` / `3.0` | Per-host rate limit (seconds) |
| `LEADHUNTER_RETRIES` | `3` | HTTP retries with exponential backoff |
| `LEADHUNTER_CONCURRENCY` | `4` | Concurrency hint |
| `LEADHUNTER_PROXY_ENABLED` | `false` | Enable proxy rotation |
| `LEADHUNTER_PROXIES` | *(empty)* | Comma-separated `http://user:pass@host:port` |

---

## 🛠️ CLI reference

```bat
python -m leadhunter scan      --query "..." --city ... --country ...
python -m leadhunter dashboard [--port 8501]
python -m leadhunter export    --format csv --city Cairo
python -m leadhunter report    --period weekly
python -m leadhunter schedule  --preset daily weekly monthly
python -m leadhunter db-stats
```

---

## 🧪 Tests

```bat
python -m pytest tests -v
```

All tests are offline — they cover text/phone/email/scoring/utils, the SQLite
DAL, and the exporters. No network required.

---

## 🔁 Data sources

| Source | Use |
|--------|-----|
| **OpenStreetMap** (Overpass + Nominatim) | Geo-targeted structured business data — Google Maps alternative, no API key |
| **DuckDuckGo + Google HTML** | Broad web discovery of company sites |
| **YellowPages / HotFrog** | Public directory listings |
| **Company websites** | Deep enrichment of contact details |

All sources are public and require no API keys. Add new sources by
subclassing `BaseHTTPClient` and returning `Lead` lists.

---

## 🪟 Running 24/7 on a Windows VPS

Two options:

1. **Console** — `run_scheduler.bat` in an always-open terminal/RDP session.
2. **Service** — wrap with [NSSM](https://nssm.cc/) so it survives reboots:

```bat
nssm install LeadHunter "C:\Python314\python.exe" "-m leadhunter schedule --preset daily weekly monthly"
nssm start LeadHunter
```

Or use **Windows Task Scheduler** to run `run_scheduler.bat` at logon.

---

## ⚖️ Compliance & ethics

This tool only collects **publicly published** business contact information
from websites and public directories. Respect each site's `robots.txt` and
terms of service. Rate limits are conservative by default — don't disable
them. Use collected data in accordance with applicable laws (e.g. GDPR,
Egyptian Personal Data Protection Law No. 151 of 2020).

---

## 📦 Tech stack

Python 3.10+ · Streamlit · Plotly · pandas · SQLite · BeautifulSoup ·
`phonenumbers` · APScheduler · `requests`.
