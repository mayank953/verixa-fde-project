
"""
agents/itr_credit_agent.py
===========================
Tax declarations and credit risk analysis using Apify SDK.
Actor: nFJndFXA5zjCTuudP  (Google Search Scraper)
Exclusive to: reviewer1 + admin
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
            "enablePerplexity":     False,
            "searchRecency":        "year",
            "returnImages":         False,
            "returnRelatedQuestions": False,
        },
        "chatGptSearch":                  {"enableChatGpt": False},
        "maximumLeadsEnrichmentRecords":  0,
        "leadsEnrichmentDepartments":     ["legal"],
        "focusOnPaidAds":                 False,
        "countryCode":                    os.getenv("SEARCH_COUNTRY", "us"),
        "searchLanguage":                 "",
        "languageCode":                   "",
        "locationUule":                   "",
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

    # Each item is one query result — extract organic results
    results_list = []
    for item in items:
        organic = item.get("organicResults", [])
        results_list.extend(organic)
    return results_list


def _parse_itr_signals(results: list[dict], name: str) -> dict:
    """Parse tax and credit risk signals from search results."""
    risk_flags = []
    good_signals = []

    risk_keywords = [
        "tax evasion", "tax fraud", "tax penalty notice",
        "shell company", "sanctions",
        "tax investigation", "money laundering",
        "loan default", "debt recovery",
        "bankruptcy", "insolvency",
        "financial fraud", "scam", "cheating case",
    ]
    good_keywords = [
        "tax return filed", "tax compliant", "vat registered", "taxpayer",
        "annual report", "director", "chartered accountant",
        "company registration", "licensed",
    ]

    for result in results:
        title   = (result.get("title") or "").lower()
        snippet = (result.get("description") or result.get("snippet") or "").lower()
        text    = title + " " + snippet

        for kw in risk_keywords:
            if kw in text:
                risk_flags.append(
                    f"Risk signal detected: '{kw}' found in web records for {name}"
                )
                break  # one flag per result

        for kw in good_keywords:
            if kw in text:
                good_signals.append(
                    f"Positive signal: '{kw}' found — indicates financial compliance"
                )
                break

    # Dedup
    risk_flags   = list(dict.fromkeys(risk_flags))[:5]
    good_signals = list(dict.fromkeys(good_signals))[:5]

    return {
        "itr_risk_flags":  risk_flags,
        "itr_good_signals": good_signals,
        "results_scanned": len(results),
    }


def _income_vs_txn_analysis(state: dict, declared_income: float) -> dict:
    """Analyze income consistency from SQL findings already in state."""
    sql        = state.get("sql_findings", {}) or {}
    txn_total  = float(sql.get("total_amount", 0) or 0)
    txn_count  = int(sql.get("transaction_count", 0) or 0)
    anomalies  = sql.get("anomaly_flags", []) or []

    signals    = []
    risk_flags = []
    score      = 0.1

    if declared_income > 0:
        annual = declared_income * 12
        ratio  = txn_total / annual if annual > 0 else 0

        if ratio > 5:
            risk_flags.append(
                f"Transaction volume (${txn_total:,.0f}) is {ratio:.1f}x annual declared "
                f"income (${annual:,.0f}) — severe income underreporting suspected"
            )
            score += 0.4
        elif ratio > 3:
            risk_flags.append(
                f"Transaction volume {ratio:.1f}x declared annual income — "
                f"possible undisclosed income sources"
            )
            score += 0.25
        elif ratio > 1:
            risk_flags.append(
                f"Transaction volume exceeds declared annual income by {ratio:.1f}x"
            )
            score += 0.1
        else:
            signals.append(
                f"Transaction volume consistent with declared income (ratio {ratio:.2f}x)"
            )
    else:
        if txn_total > 20000:
            risk_flags.append(
                f"Zero declared income but ${txn_total:,.0f} in transactions — "
                f"income source completely undisclosed"
            )
            score += 0.4

    if "STRUCTURING_PATTERN" in str(anomalies):
        risk_flags.append("Structuring pattern detected — classic income concealment technique")
        score += 0.25

    if declared_income > 0:
        signals.append(f"Declared monthly income: ${declared_income:,.0f}")
        signals.append(f"Declared annual income:  ${declared_income*12:,.0f}")

    return {
        "declared_income":    declared_income,
        "annual_income_est":  declared_income * 12,
        "txn_volume":         txn_total,
        "income_signals":     signals,
        "income_risk_flags":  risk_flags,
        "income_score":       round(min(score, 0.95), 3),
    }


async def run_itr_credit_agent(state: dict[str, Any]) -> dict[str, Any]:
    """
    Tax and credit risk agent (internal id: itr_credit).
    1. Google search via Apify (actor nFJndFXA5zjCTuudP) for tax/credit risk signals
    2. Income vs transaction analysis from SQL findings
    """
    entity_id = state.get("entity_id", "")
    customer  = state.get("customer_info", {}) or {}
    name      = customer.get("name", entity_id)
    city      = customer.get("city", "")
    declared  = float(customer.get("monthly_income", 0) or 0)

    logger.info(f"[Tax/Credit] Analyzing {entity_id} — {name}")

    # ── SQL income analysis (no API needed) ───────────
    income_analysis = _income_vs_txn_analysis(state, declared)

    # ── Google search for tax and credit signals ──────────
    query = f'"{name}" {city} income tax credit fraud default financial'
    itr_signals = {"itr_risk_flags": [], "itr_good_signals": [], "results_scanned": 0}

    if not APIFY_TOKEN and not providers.sample():
        logger.error("[Tax/Credit] APIFY_API_TOKEN not set — skipping web search")
        itr_signals["itr_risk_flags"].append("Web search skipped — APIFY_API_TOKEN missing")
    else:
        try:
            client = providers.apify_client(APIFY_TOKEN, "google")
            logger.info(f"[Tax/Credit] Running actor {ACTOR_ID} — query: {query}")
            search_results = _run_google_search(client, query, results=10)
            logger.info(f"[Tax/Credit] Got {len(search_results)} results")
            itr_signals = _parse_itr_signals(search_results, name)
        except Exception as e:
            logger.error(f"[Tax/Credit] Apify error: {e}")
            itr_signals["itr_risk_flags"].append(f"Web search failed: {e}")

    # ── 5-year income assessment ───────────────────────
    five_year = []
    if declared > 0:
        five_year = [
            f"Year 1-5 declared: ${declared:,.0f}/mo → ${declared*12:,.0f}/yr",
            f"DB transaction volume: ${income_analysis['txn_volume']:,.0f}",
            f"Consistency ratio: {income_analysis['txn_volume']/(declared*12):.2f}x" if declared > 0 else "N/A",
            "Note: full tax history requires an official tax-authority integration",
        ]
    else:
        five_year = ["Zero declared income — tax history indeterminate"]

    all_risk  = income_analysis["income_risk_flags"] + itr_signals["itr_risk_flags"]
    all_good  = income_analysis["income_signals"]    + itr_signals["itr_good_signals"]

    itr_data = {
        "declared_income":        declared,
        "annual_income_est":      income_analysis["annual_income_est"],
        "txn_volume":             income_analysis["txn_volume"],
        "five_year_assessment":   five_year,
        "income_consistency":     all_good,
        "risk_flags":             all_risk,
        "itr_web_results_count":  itr_signals["results_scanned"],
        "itr_credit_score":       round(min(
            income_analysis["income_score"] + len(all_risk) * 0.05, 0.95
        ), 3),
        "source": "apify+sql",
    }

    existing = state.get("fraud_indicators", []) or []
    for flag in all_risk:
        existing.append({
            "source":      "itr_credit",
            "flag":        "ITR_INCOME_RISK",
            "description": flag,
        })

    return {**state, "itr_credit_findings": itr_data, "fraud_indicators": existing}