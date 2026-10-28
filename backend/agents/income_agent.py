"""
agents/income_agent.py
======================
Income Credentials + Lifestyle Signals agent using Apify SDK.
Actor: nFJndFXA5zjCTuudP  (Google Search Scraper — same as the tax agent)
Exclusive to: reviewer2 + admin
"""

import os
from typing import Any

from backend import providers

from backend.logger import GLOBAL_LOGGER as logger

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
ACTOR_ID    = os.getenv("GOOGLE_SEARCH_ACTOR_ID","")   # Google Search Scraper


def _run_google_search(client, query: str, results: int = 10) -> list[dict]:
    """Run Apify Google Search Scraper and return organic results."""
    run_input = {
        "queries":                        query,
        "resultsPerPage":                 results,
        "maxPagesPerQuery":               1,
        "disableGoogleSearchResults":     False,
        "aiModeSearch":                   {"enableAiMode": False},
        "perplexitySearch": {
            "enablePerplexity":       False,
            "searchRecency":          "year",
            "returnImages":           False,
            "returnRelatedQuestions": False,
        },
        "chatGptSearch":                  {"enableChatGpt": False},
        "maximumLeadsEnrichmentRecords":  0,
        "leadsEnrichmentDepartments":     ["legal"],
        "focusOnPaidAds":                 False,
        "countryCode":                    os.getenv("SEARCH_COUNTRY", "us"),
        "searchLanguage":                 "",
        "languageCode":                   "",
        "forceExactMatch":                False,
        "wordsInTitle":                   [],
        "wordsInText":                    [],
        "wordsInUrl":                     [],
        "mobileResults":                  False,
        "includeUnfilteredResults":       False,
        "saveHtml":                       False,
        "saveHtmlToKeyValueStore":        True,
        "includeIcons":                   False,
    }

    run   = client.actor(ACTOR_ID).call(run_input=run_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

    results_list = []
    for item in items:
        organic = item.get("organicResults", [])
        results_list.extend(organic)
    return results_list


def _parse_income_signals(results: list[dict], name: str, declared: float) -> dict:
    """Parse good income credentials and lifestyle signals from search results."""
    good_credentials = []
    lifestyle_signals = []
    risk_flags       = []
    credential_score  = 0.0

    good_keywords = [
        "promoted", "award", "recognition", "founded", "startup",
        "director", "ceo", "cto", "cfo", "patent", "published",
        "property registered", "house purchase", "investment",
        "salary hike", "appraisal", "increment", "bonus",
        "company registration", "licensed",
    ]
    luxury_keywords = [
        "supercar", "private jet", "yacht", "villa", "bungalow",
        "penthouse", "luxury", "lavish", "extravagant", "expensive lifestyle",
        "high-end", "billionaire", "luxury property",
    ]

    for result in results:
        title   = (result.get("title") or "").lower()
        snippet = (result.get("description") or result.get("snippet") or "").lower()
        text    = title + " " + snippet

        for kw in good_keywords:
            if kw in text:
                good_credentials.append(
                    f"Good credential: '{kw}' found — supports income claim for {name}"
                )
                credential_score += 0.1
                break

        for kw in luxury_keywords:
            if kw in text:
                lifestyle_signals.append(
                    f"Luxury signal: '{kw}' found — verify against "
                    f"declared income ${declared:,.0f}/mo"
                )
                break

    # Dedup
    good_credentials  = list(dict.fromkeys(good_credentials))[:5]
    lifestyle_signals = list(dict.fromkeys(lifestyle_signals))[:5]

    # Lifestyle vs income check
    if lifestyle_signals and declared < 4000:
        risk_flags.append(
            f"Luxury lifestyle signals detected but declared income only "
            f"${declared:,.0f}/mo — possible undisclosed wealth"
        )

    return {
        "good_credentials":  good_credentials,
        "lifestyle_signals": lifestyle_signals,
        "risk_flags":        risk_flags,
        "credential_score":  round(min(credential_score, 1.0), 3),
        "results_scanned":   len(results),
    }


def _income_jump_check(state: dict, declared: float) -> dict:
    """Check for sudden income jump from transaction patterns."""
    sql       = state.get("sql_findings", {}) or {}
    txn_total = float(sql.get("total_amount", 0) or 0)
    txn_count = int(sql.get("transaction_count", 0) or 0)
    anomalies = sql.get("anomaly_flags", []) or []

    signals    = []
    risk_flags = []

    if declared > 0 and txn_total > 0:
        annual = declared * 12
        ratio  = txn_total / annual

        if ratio > 5:
            risk_flags.append(
                f"Sudden wealth indicator: txn volume {ratio:.1f}x annual declared income — "
                "verify recent tax returns for an income jump"
            )
        elif ratio > 2:
            risk_flags.append(
                f"Possible income jump: txn volume {ratio:.1f}x annual income — "
                "check previous tax returns"
            )
        else:
            signals.append(
                f"Income appears consistent with transaction volume (ratio {ratio:.2f}x)"
            )

    if declared == 0 and txn_total > 20000:
        risk_flags.append(
            f"Zero declared income with ${txn_total:,.0f} in transactions — "
            "income source completely unknown"
        )

    return {"signals": signals, "risk_flags": risk_flags}


async def run_income_agent(state: dict[str, Any]) -> dict[str, Any]:
    """
    Income Credentials agent.
    1. Google search via Apify for good income credentials and lifestyle signals
    2. Income jump analysis from SQL data
    """
    entity_id  = state.get("entity_id", "")
    customer   = state.get("customer_info", {}) or {}
    name       = customer.get("name", entity_id)
    city       = customer.get("city", "")
    declared   = float(customer.get("monthly_income", 0) or 0)
    occupation = customer.get("occupation", "")

    logger.info(f"[Income] Analyzing {entity_id} — {name}, {occupation}")

    # ── SQL jump analysis ──────────────────────────────
    jump = _income_jump_check(state, declared)

    # ── Google search for income credentials ───────────
    query = f'"{name}" {city} {occupation} income salary wealth property award'
    signal_analysis = {
        "good_credentials": [], "lifestyle_signals": [],
        "risk_flags": [], "credential_score": 0.0, "results_scanned": 0
    }

    if not APIFY_TOKEN and not providers.sample():
        logger.error("[Income] APIFY_API_TOKEN not set — skipping web search")
        signal_analysis["risk_flags"].append("Web search skipped — APIFY_API_TOKEN missing")
    else:
        try:
            client = providers.apify_client(APIFY_TOKEN, "google")
            logger.info(f"[Income] Running actor {ACTOR_ID} — query: {query}")
            search_results  = _run_google_search(client, query, results=10)
            logger.info(f"[Income] Got {len(search_results)} results")
            signal_analysis = _parse_income_signals(search_results, name, declared)
        except Exception as e:
            logger.error(f"[Income] Apify error: {e}")
            signal_analysis["risk_flags"].append(f"Web search failed: {e}")

    all_risk = signal_analysis["risk_flags"] + jump["risk_flags"]
    all_good = signal_analysis["good_credentials"] + jump["signals"]

    income_data = {
        "declared_monthly_income":  declared,
        "occupation":               occupation,
        "good_credentials":         all_good,
        "lifestyle_signals":        signal_analysis["lifestyle_signals"],
        "risk_flags":               all_risk,
        "income_credential_score":  signal_analysis["credential_score"],
        "web_results_scanned":      signal_analysis["results_scanned"],
        "source":                   "apify+sql",
    }

    existing = state.get("fraud_indicators", []) or []
    for flag in all_risk:
        existing.append({
            "source":      "income",
            "flag":        "INCOME_MISMATCH",
            "description": flag,
        })

    return {**state, "income_findings": income_data, "fraud_indicators": existing}