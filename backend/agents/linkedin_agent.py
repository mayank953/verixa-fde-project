
"""
agents/linkedin_agent.py
========================
LinkedIn profile investigator using Apify SDK.
Actor: mSiesbz781McL0tYy
Exclusive to: reviewer1 + admin
"""

import os
from typing import Any

from backend import providers
from backend.logger import GLOBAL_LOGGER as logger

APIFY_TOKEN   = os.getenv("APIFY_API_TOKEN", "")
ACTOR_ID      = os.getenv("LINKEDIN_ACTOR_ID","")



def _build_linkedin_url(name: str, city: str = "") -> str:
    """
    Build a best-guess LinkedIn search URL from customer name.
    Agent uses name as search query — Apify actor accepts profile URL or name.
    """
    # Format name for LinkedIn search URL
    slug = name.lower().replace(" ", "-")
    return f"https://www.linkedin.com/in/{slug}"


def _analyze_profile(item: dict, declared_income: float) -> dict:
    """Extract fraud signals from a LinkedIn profile result."""
    risk_flags    = []
    income_signals = []
    score         = 0.1  # base — profile found

    name        = item.get("fullName") or item.get("name", "")
    headline    = item.get("headline", "") or ""
    connections = item.get("connectionsCount") or item.get("connections", 0) or 0
    jobs        = item.get("positions") or item.get("experience") or []
    education   = item.get("educations") or item.get("education") or []
    email       = item.get("email", "")

    # ── Employment checks ──────────────────────────────
    if not jobs:
        risk_flags.append("No employment history on LinkedIn — high anonymity risk")
        score += 0.3
    else:
        current = jobs[0]
        title   = current.get("title", "Unknown")
        company = current.get("companyName", "Unknown")
        income_signals.append(f"Current role: {title} at {company}")
        income_signals.append(f"Total jobs listed: {len(jobs)}")

        if len(jobs) > 10:
            risk_flags.append("Unusually high job count — frequent switching pattern")
            score += 0.1

    # ── Connections check ──────────────────────────────
    if connections < 30:
        risk_flags.append(f"Very low connections ({connections}) — possibly fake or new profile")
        score += 0.2
    elif connections > 500:
        income_signals.append(f"Strong network ({connections}+ connections)")

    # ── Headline vs declared income ────────────────────
    suspicious_keywords = ["unknown", "unemployed", "freelancer", "student"]
    if any(k in headline.lower() for k in suspicious_keywords) and declared_income > 6000:
        risk_flags.append(
            f"Headline '{headline}' inconsistent with declared income "
            f"${declared_income:,.0f}/mo"
        )
        score += 0.2

    if not headline:
        risk_flags.append("No LinkedIn headline — profile may be incomplete or fake")
        score += 0.15

    return {
        "found":            True,
        "name":             name,
        "headline":         headline,
        "email":            email,
        "connections":      connections,
        "employment_count": len(jobs),
        "jobs":             jobs[:5],
        "education":        education[:3],
        "income_signals":   income_signals,
        "risk_flags":       risk_flags,
        "linkedin_score":   round(min(score, 0.95), 3),
        "raw_item":         item,
        "source":           "apify",
    }


def _no_profile(name: str, reason: str) -> dict:
    return {
        "found":            False,
        "name":             name,
        "headline":         None,
        "email":            None,
        "connections":      0,
        "employment_count": 0,
        "jobs":             [],
        "education":        [],
        "income_signals":   [],
        "risk_flags":       [f"LinkedIn profile not found: {reason}"],
        "linkedin_score":   0.5,
        "source":           "apify",
    }


async def run_linkedin_agent(state: dict[str, Any]) -> dict[str, Any]:
    """
    LinkedIn agent — hits Apify actor mSiesbz781McL0tYy.
    Searches by customer name, extracts employment + income signals.
    """
    entity_id  = state.get("entity_id", "")
    customer   = state.get("customer_info", {}) or {}
    name       = customer.get("name", entity_id)
    city       = customer.get("city", "")
    income     = float(customer.get("monthly_income", 0) or 0)

    logger.info(f"[LinkedIn] Investigating {entity_id} — {name}")

    if not APIFY_TOKEN and not providers.sample():
        logger.error("[LinkedIn] APIFY_API_TOKEN not set — cannot run real scrape")
        linkedin_data = _no_profile(name, "APIFY_API_TOKEN missing")
        return {**state, "linkedin_findings": linkedin_data,
                "fraud_indicators": state.get("fraud_indicators", [])}

    try:
        client = providers.apify_client(APIFY_TOKEN, "linkedin")

        linkedin_url = _build_linkedin_url(name, city)

        run_input = {
            "linkedin_url":  linkedin_url,
            "extract_email": True,
        }

        logger.info(f"[LinkedIn] Running actor {ACTOR_ID} for URL: {linkedin_url}")
        run = client.actor(ACTOR_ID).call(run_input=run_input)

        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info(f"[LinkedIn] Got {len(items)} results")

        if not items:
            linkedin_data = _no_profile(name, "No profile found on LinkedIn")
        else:
            linkedin_data = _analyze_profile(items[0], income)

    except Exception as e:
        logger.error(f"[LinkedIn] Apify error for {entity_id}: {e}")
        linkedin_data = _no_profile(name, str(e))

    # Append to fraud indicators
    existing = state.get("fraud_indicators", []) or []
    for flag in linkedin_data.get("risk_flags", []):
        existing.append({
            "source":      "linkedin",
            "flag":        "LINKEDIN_RISK",
            "description": flag,
        })

    return {**state, "linkedin_findings": linkedin_data, "fraud_indicators": existing}