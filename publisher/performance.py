"""Search Console + GA4 reporting, and "needs improvement" flags.

Auth: a Google Cloud service account JSON in GOOGLE_SERVICE_ACCOUNT_JSON.
Add the service account e-mail as a (restricted) user on the Search Console
property and as a Viewer on the GA4 property. Read-only scopes only.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from urllib.parse import quote

import requests

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly",
          "https://www.googleapis.com/auth/analytics.readonly"]


def _token() -> str | None:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


def gsc_query(site: str, dimensions: list[str], days: int = 90, row_limit: int = 1000) -> list[dict]:
    tok = _token()
    if not tok:
        return []
    end = date.today() - timedelta(days=3)  # GSC data lags ~2-3 days
    body = {"startDate": str(end - timedelta(days=days)), "endDate": str(end),
            "dimensions": dimensions, "rowLimit": row_limit}
    r = requests.post(f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}/searchAnalytics/query",
                      headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=60)
    r.raise_for_status()
    return r.json().get("rows", [])


def ga4_pages(property_id: str, days: int = 28) -> list[dict]:
    tok = _token()
    if not (tok and property_id):
        return []
    body = {"dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "yesterday"}],
            "dimensions": [{"name": "pagePath"}],
            "metrics": [{"name": "screenPageViews"}, {"name": "userEngagementDuration"}, {"name": "engagementRate"}],
            "limit": 1000}
    r = requests.post(f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
                      headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=60)
    r.raise_for_status()
    out = []
    for row in r.json().get("rows", []):
        out.append({"path": row["dimensionValues"][0]["value"],
                    "views": float(row["metricValues"][0]["value"]),
                    "engagement_seconds": float(row["metricValues"][1]["value"]),
                    "engagement_rate": float(row["metricValues"][2]["value"])})
    return out


def demand_notes(cfg: dict) -> str:
    """Queries people already find New Earns for, where no page ranks well yet."""
    rows = gsc_query(cfg["tracking"]["gsc_site_url"], ["query"], days=90)
    gaps = [r for r in rows if r["impressions"] >= 20 and r["position"] > 10]
    gaps.sort(key=lambda r: -r["impressions"])
    return "\n".join(f"- '{r['keys'][0]}': {int(r['impressions'])} impressions, avg position {r['position']:.1f}"
                     for r in gaps[:25])


def improvement_flags(cfg: dict, published: list[dict]) -> list[dict]:
    """Rules applied to each logged article older than `refresh_after_days`."""
    rows = {r["keys"][0].rstrip("/"): r for r in gsc_query(cfg["tracking"]["gsc_site_url"], ["page"], days=90)}
    flags, today = [], date.today()
    for a in published:
        if a.get("status") != "published":
            continue
        age = (today - date.fromisoformat(a["date"][:10])).days
        if age < cfg["tracking"]["refresh_after_days"]:
            continue
        r = rows.get(a["url"].rstrip("/"))
        if not r:
            flags.append({**a, "flag": "no_impressions", "advice": "check indexing in Search Console; consider merging or retargeting"})
        elif r["position"] <= 10 and r["ctr"] < 0.01 and r["impressions"] > 200:
            flags.append({**a, "flag": "low_ctr", "advice": "rewrite title and meta description", "gsc": r})
        elif 8 < r["position"] <= 25:
            flags.append({**a, "flag": "striking_distance", "advice": "refresh content, add missing subtopics and internal links", "gsc": r})
    return flags
