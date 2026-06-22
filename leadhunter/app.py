"""
LeadHunter Streamlit dashboard.

Pages:
  * Overview  — KPIs + charts (industry / city / source / score distribution)
  * Search    — run a new lead-generation search
  * Leads     — browse, filter, and export the full lead database
  * Schedule  — manage daily/weekly/monthly scans & recurring searches
  * Reports   — daily/weekly summaries + missing-contact lists

Run with:  streamlit run app.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import List

import pandas as pd
import plotly.express as px
import streamlit as st

# Make the package importable when running `streamlit run app.py` from
# the project root (file lives at <root>/leadhunter/app.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leadhunter.config import get_config
from leadhunter.core.database import Database
from leadhunter.core.exporters import export_csv, export_excel
from leadhunter.pipeline import Pipeline, SearchCriteria
from leadhunter.reporting import build_report, write_report
from leadhunter.scheduler import (load_searches, save_searches,
                                  run_scheduled_scan, get_scheduler, PRESETS)
from leadhunter.utils.logger import get_logger

log = get_logger(__name__)
cfg = get_config()

# ---------------------------------------------------------------------------
# Page config + shared helpers
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="LeadHunter — Lead Generation",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_db() -> Database:
    return Database()


@st.cache_resource
def get_pipe() -> Pipeline:
    return Pipeline(db=get_db())


@st.cache_data(ttl=30)
def load_stats():
    return get_db().stats()


def metric_card(label, value, delta=None):
    st.metric(label, value, delta)


def _bar_chart(data: dict, title: str, color: str = "#1f77b4"):
    if not data:
        st.caption("No data yet.")
        return
    df = pd.DataFrame({"label": list(data.keys()), "count": list(data.values())})
    fig = px.bar(df, x="count", y="label", orientation="h",
                 title=title, color_discrete_sequence=[color],
                 template="plotly_white")
    fig.update_layout(yaxis=dict(autorange="reversed"), height=320,
                      margin=dict(l=8, r=8, t=40, b=8))
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
st.sidebar.title("🎯 LeadHunter")
st.sidebar.caption(f"v1.0 · {cfg.default_country}")
page = st.sidebar.radio(
    "Navigate", ["📊 Overview", "🔍 New Search", "📋 Leads",
                 "⏰ Schedule", "📈 Reports"],
    index=0)
st.sidebar.markdown("---")
db_total = get_db().count()
st.sidebar.metric("Leads in DB", db_total)


# ===========================================================================
# Overview page
# ===========================================================================
if page == "📊 Overview":
    st.title("📊 Overview")
    stats = load_stats()
    totals = get_db().counts_since((datetime.now(timezone.utc).isoformat(timespec="seconds")))

    c1, c2, c3, c4, c5 = st.columns(5)
    metric_card("Total Leads", stats["total"])
    metric_card("Avg Score", stats.get("avg_score", 0))
    metric_card("Industries", len(stats.get("by_industry", {})))
    metric_card("Cities", len(stats.get("by_city", {})))
    metric_card("Sources", len(stats.get("by_source_host", {})))

    left, right = st.columns(2)
    with left:
        _bar_chart(stats.get("by_industry", {}), "Leads by Industry", "#1f77b4")
        _bar_chart(stats.get("by_city", {}), "Leads by City", "#2ca02c")
    with right:
        _bar_chart(stats.get("by_source_host", {}), "Leads by Source", "#ff7f0e")
        _bar_chart(stats.get("by_tier", {}), "Leads by Quality Tier", "#9467bd")

    st.caption(f"Last refresh: {datetime.now():%Y-%m-%d %H:%M:%S}")


# ===========================================================================
# New Search page
# ===========================================================================
elif page == "🔍 New Search":
    st.title("🔍 New Search")
    st.markdown("Find businesses by industry, keyword, governorate, city, or radius.")
    with st.form("search_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            query = st.text_input(
                "Search query / keyword *",
                value="Dental Clinics",
                help="e.g. 'Dental Clinics', 'Car Dealerships', 'Software Companies'")
            industry = st.text_input("Industry (optional)", "Dental & Healthcare")
        with col2:
            city = st.text_input("City", value="Asyut")
            governorate = st.text_input("Governorate", "")
            country = st.text_input("Country", value=cfg.default_country)
        with col3:
            radius = st.number_input("Radius (km, 0 = default 8km)",
                                     min_value=0, max_value=100, value=0)
            limit = st.number_input("Max results per source", 10, 200, 50, 10)
            enrich = st.checkbox("Deep enrich (visit websites)", value=True)
        sources = st.multiselect(
            "Sources",
            ["openstreetmap", "search", "directories"],
            default=["openstreetmap", "search", "directories"])
        submitted = st.form_submit_button("🚀 Run Search", type="primary")

    if submitted:
        crit = SearchCriteria(
            query=query, industry=industry, category=query,
            city=city, governorate=governorate, country=country,
            radius_km=radius or None, limit=limit, enrich=enrich, sources=sources)
        with st.spinner(f"Searching for '{query}' in {city or country}…"):
            result = get_pipe().run(crit)
        st.success(f"Done! {result.summary()}")
        st.write(f"- Discovered: **{result.discovered}** "
                 f"(inserted **{result.inserted}**, updated **{result.updated}**)")
        if result.leads:
            df = pd.DataFrame([{
                "Company": l.company_name, "Industry": l.industry,
                "City": l.city, "Website": l.website, "Email": l.email,
                "Phone": l.phone, "Score": l.confidence_score,
            } for l in result.leads])
            st.dataframe(df, use_container_width=True, height=350)

    st.markdown("---")
    st.subheader("Quick examples")
    examples = [
        ("Dental Clinics", "Asyut", "Egypt"),
        ("Car Dealerships", "Cairo", "Egypt"),
        ("Software Companies", "Alexandria", "Egypt"),
        ("Construction Companies", "Giza", "Egypt"),
        ("Logistics Companies", "", "Egypt"),
    ]
    cols = st.columns(len(examples))
    for col, (q, c, co) in zip(cols, examples):
        if col.button(f"{q}\n{c or co}", use_container_width=True, key=f"ex_{q}_{c}"):
            st.session_state["example"] = (q, c, co)
            st.rerun()
    # Pre-fill the form if an example was clicked
    if "example" in st.session_state and st.session_state["example"]:
        # can't easily mutate the form post-fact; just inform the user.
        q, c, co = st.session_state["example"]
        st.info(f"Example selected: **{q}** in **{c or co}** — fill the form above and click Run.")


# ===========================================================================
# Leads browser page
# ===========================================================================
elif page == "📋 Leads":
    st.title("📋 Leads Database")
    db = get_db()

    with st.expander("🔎 Filters", expanded=True):
        fcol1, fcol2, fcol3, fcol4 = st.columns(4)
        with fcol1:
            f_industry = st.text_input("Industry", key="f_ind")
            f_city = st.text_input("City", key="f_city")
        with fcol2:
            f_gov = st.text_input("Governorate", key="f_gov")
            f_country = st.text_input("Country", key="f_country")
        with fcol3:
            f_source = st.text_input("Source contains", key="f_src")
            f_keyword = st.text_input("Keyword", key="f_kw")
        with fcol4:
            f_min_score = st.slider("Min confidence score", 0, 100, 0, 5)
            f_limit = st.selectbox("Rows", [100, 250, 500, 1000, 0], index=1,
                                   format_func=lambda x: "All" if x == 0 else str(x))

    leads = db.search(
        industry=f_industry, city=f_city, governorate=f_gov, country=f_country,
        source=f_source, keyword=f_keyword, min_score=f_min_score,
        limit=None if f_limit == 0 else f_limit)

    st.caption(f"Showing {len(leads)} of {db.count()} leads")
    if leads:
        df = pd.DataFrame([l.to_dict() for l in leads])
        # Drop noisy columns for the on-screen view
        drop = [c for c in ("lead_id", "dedup_key", "discovered_at",
                            "updated_at") if c in df.columns]
        st.dataframe(df.drop(columns=drop), use_container_width=True, height=520)

        ecol1, ecol2 = st.columns(2)
        with ecol1:
            if st.button("⬇️ Export CSV"):
                path = export_csv(leads)
                with open(path, "rb") as f:
                    st.download_button("Download CSV", f,
                                       file_name=os.path.basename(path),
                                       mime="text/csv")
        with ecol2:
            if st.button("⬇️ Export Excel"):
                path = export_excel(leads)
                with open(path, "rb") as f:
                    st.download_button("Download XLSX", f,
                                       file_name=os.path.basename(path),
                                       mime="application/vnd.openxmlformats-"
                                            "officedocument.spreadsheetml.sheet")
    else:
        st.info("No leads match these filters. Try running a search from the "
                "**New Search** tab.")


# ===========================================================================
# Schedule page
# ===========================================================================
elif page == "⏰ Schedule":
    st.title("⏰ Scheduling")
    st.markdown("Configure recurring scans that keep your lead database fresh. "
                "These run inside the dashboard process via APScheduler.")

    sch = get_scheduler()
    try:
        sch.start()
    except Exception:
        pass

    st.subheader("Active schedules")
    jobs = sch.jobs_info()
    if jobs:
        jdf = pd.DataFrame(jobs)
        st.dataframe(jdf, use_container_width=True, hide_index=True)
    else:
        st.caption("No schedules active.")

    scol1, scol2, scol3, scol4 = st.columns(4)
    with scol1:
        if st.button("➕ Enable Daily"):
            sch.schedule("daily"); sch.start(); st.success("Daily scan enabled")
    with scol2:
        if st.button("➕ Enable Weekly"):
            sch.schedule("weekly"); sch.start(); st.success("Weekly scan enabled")
    with scol3:
        if st.button("➕ Enable Monthly"):
            sch.schedule("monthly"); sch.start(); st.success("Monthly scan enabled")
    with scol4:
        if st.button("▶️ Run Now"):
            with st.spinner("Running all saved searches…"):
                res = sch.run_now("manual")
            st.success(f"Done! Inserted {res['totals']['inserted']}, "
                       f"updated {res['totals']['updated']}")

    st.markdown("---")
    st.subheader("Recurring searches")
    st.caption("These are run by every scheduled scan. Edit freely.")
    searches = load_searches()
    edited = st.data_editor(
        pd.DataFrame(searches),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "query": st.column_config.TextColumn("Query", required=True),
            "city": st.column_config.TextColumn("City"),
            "governorate": st.column_config.TextColumn("Governorate"),
            "country": st.column_config.TextColumn("Country"),
            "industry": st.column_config.TextColumn("Industry"),
            "limit": st.column_config.NumberColumn("Limit", min_value=5, max_value=200, step=5),
        },
        hide_index=True,
    )
    if st.button("💾 Save searches"):
        save_searches(edited.to_dict(orient="records"))
        st.success(f"Saved {len(edited)} searches.")


# ===========================================================================
# Reports page
# ===========================================================================
elif page == "📈 Reports":
    st.title("📈 Reports")
    db = get_db()
    rcol1, rcol2 = st.columns(2)
    days = st.selectbox("Period", [1, 7, 30], format_func=lambda d: {
        1: "Last day", 7: "Last 7 days", 30: "Last 30 days"}[d])
    label = {1: "Daily", 7: "Weekly", 30: "Monthly"}[days]
    if st.button("📊 Generate report"):
        with st.spinner("Building report…"):
            report = build_report(db, days=days, label=label)
            txt, jsn = write_report(report)
        st.success(f"Report written to:\n- {txt}\n- {jsn}")
        st.subheader("Summary")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total in DB", report.totals["total"])
        k2.metric("New this period", report.totals["new"])
        k3.metric("Updated", report.totals["updated"])
        k4.metric("Missing contact", report.totals["missing_contact"])
        st.text(_render_report_text(report))

    st.markdown("---")
    st.subheader("Past reports")
    if os.path.isdir(cfg.reports_dir):
        files = sorted(
            [f for f in os.listdir(cfg.reports_dir)
             if f.endswith((".txt", ".json"))], reverse=True)[:30]
        if files:
            sel = st.selectbox("Open a report", files)
            with open(os.path.join(cfg.reports_dir, sel), "r", encoding="utf-8") as f:
                content = f.read()
            if sel.endswith(".json"):
                st.json(content)
            else:
                st.text(content)
        else:
            st.caption("No reports generated yet.")
    else:
        st.caption("No reports directory yet.")


def _render_report_text(report) -> str:
    from leadhunter.reporting import _format_text
    return _format_text(report)
