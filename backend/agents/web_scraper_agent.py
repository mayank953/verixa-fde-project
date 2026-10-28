"""
Web Scraper Agent — Production APIs
=====================================
Real APIs used:

1. SerpAPI           (serpapi.com)             — Google search intelligence
   Free: 100 searches/month | Signup: serpapi.com
   Key:  SERP_API_KEY
   Runs 3 targeted searches per entity:
     - "{name} fraud scam arrested" 
     - "{name} court case lawsuit"
     - "{name} money laundering financial crime"

2. XposedOrNot      (api.xposedornot.com)      — data breach check
   Free: completely free, NO key needed

3. Disify           (disify.com)               — disposable email check
   Free: completely free, NO key needed

If a key is missing → graceful fallback (score not inflated).
"""

from __future__ import annotations
import asyncio
import os
import httpx
from backend import providers
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime

import psycopg2
from backend.models.model import WebIntelligenceSynthesis
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file
from backend.logger import GLOBAL_LOGGER as logger
# ── API Keys from env ──────────────────────────────────────────
SERP_API_KEY = os.getenv("SERP_API_KEY", "")
# XposedOrNot and Disify need no key — always available


# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# 1. SerpAPI — Google Search Intelligence
#    Free: 100 searches/month | Signup: serpapi.com
#    Runs 3 targeted queries per entity:
#      Q1: "{name} fraud scam arrested"
#      Q2: "{name} court case lawsuit"
#      Q3: "{name} money laundering financial crime"
# ─────────────────────────────────────────────────────────────

NEGATIVE_KEYWORDS = [
    "fraud", "scam", "arrested", "laundering", "sanctioned", "banned",
    "blacklisted", "criminal", "seized", "penalty", "indicted", "convicted",
    "embezzlement", "bribery", "forgery", "defaulter", "fugitive", "chargesheet",
    "wilful default", "money laundering", "financial crime", "court case", "lawsuit"
]

SERP_SEARCH_QUERIES = [
    "{name} fraud scam arrested",
    "{name} court case lawsuit",
    "{name} money laundering financial crime",
]


async def _serp_search(query: str) -> List[Dict]:
    """Single SerpAPI Google search. Returns top organic results."""
    url = "https://serpapi.com/search"
    params = {
        "api_key": SERP_API_KEY,
        "engine":  "google",
        "q":       query,
        "num":     5,
        "hl":      "en",
        "gl":      "us",
    }
    async with providers.async_client(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in data.get("organic_results", [])[:5]:
        results.append({
            "title":    r.get("title", ""),
            "snippet":  r.get("snippet", ""),
            "url":      r.get("link", ""),
            "source":   r.get("displayed_link", ""),
            "position": r.get("position", 0),
        })
    return results


async def fetch_serp_intelligence(entity_name: str, entity_id: str) -> Dict[str, Any]:
    """
    Run 3 targeted Google searches via SerpAPI.
    Deduplicates by URL, scores for negative keywords, returns enriched mentions.
    """
    if not SERP_API_KEY and not providers.sample():
        logger.warning("[SerpAPI] SERP_API_KEY not set — skipping Google search")
        return {
            "source":            "serpapi",
            "skipped":           True,
            "reason":            "SERP_API_KEY not configured — get free key at serpapi.com",
            "mentions":          [],
            "negative_mentions": 0,
            "risk_signal":       False
        }

    name = entity_name.strip() if entity_name and not entity_name.startswith("CUST_") else None
    if not name:
        logger.warning(f"[SerpAPI] No real name for {entity_id} — skipping")
        return {
            "source":            "serpapi",
            "skipped":           True,
            "reason":            "No real entity name available",
            "mentions":          [],
            "negative_mentions": 0,
            "risk_signal":       False
        }

    queries = [q.format(name=name) for q in SERP_SEARCH_QUERIES]
    logger.info(f"[SerpAPI] Running {len(queries)} searches for '{name}'")

    raw_results = await asyncio.gather(
        *[_serp_search(q) for q in queries],
        return_exceptions=True
    )

    # Flatten + deduplicate by URL
    seen_urls: set = set()
    all_results: List[Dict] = []
    for query, result in zip(queries, raw_results):
        if isinstance(result, Exception):
            logger.warning(f"[SerpAPI] Search failed '{query}': {result}")
            continue
        for r in result:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                r["search_query"] = query
                all_results.append(r)

    # Score each result for risk keywords
    mentions       = []
    negative_count = 0
    for r in all_results:
        combined       = f"{r.get('title','')} {r.get('snippet','')}".lower()
        found_keywords = [kw for kw in NEGATIVE_KEYWORDS if kw in combined]
        is_negative    = len(found_keywords) > 0
        if is_negative:
            negative_count += 1

        mentions.append({
            "headline":      r.get("title", ""),
            "snippet":       r.get("snippet", ""),
            "source":        r.get("source", ""),
            "url":           r.get("url", ""),
            "search_query":  r.get("search_query", ""),
            "position":      r.get("position", 0),
            "sentiment":     "negative" if is_negative else "neutral",
            "risk_keywords": found_keywords,
        })

    risk_signal = negative_count >= 1
    logger.info(f"[SerpAPI] '{name}': {len(all_results)} results, {negative_count} negative")

    return {
        "source":            "serpapi",
        "entity_id":         entity_id,
        "entity_name":       name,
        "queries_run":       queries,
        "total_results":     len(all_results),
        "mentions":          mentions,
        "negative_mentions": negative_count,
        "risk_signal":       risk_signal
    }


# ─────────────────────────────────────────────────────────────
# 2. XposedOrNot — Data breach check
#    100% Free, NO key needed | https://api.xposedornot.com
#    Drop-in replacement for HaveIBeenPwned
# ─────────────────────────────────────────────────────────────
async def check_breach_exposure(email: str, entity_id: str) -> Dict[str, Any]:
    """Check if email appears in known data breaches via XposedOrNot (free, no key)."""

    if not email or "@" not in email:
        return {
            "source":                  "xposedornot",
            "skipped":                 True,
            "reason":                  "no valid email provided",
            "breach_count":            0,
            "compromised_credentials": False,
            "risk_signal":             False
        }

    url = f"https://api.xposedornot.com/v1/check-email/{email}"

    try:
        async with providers.async_client(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "FraudAnalyticsPlatform/1.0"})

        # 404 → email not found in any breach (good!)
        if resp.status_code == 404:
            logger.info(f"[XposedOrNot] {email}: no breaches found")
            return {
                "source":                  "xposedornot",
                "entity_id":               entity_id,
                "email":                   email,
                "breach_count":            0,
                "breach_names":            [],
                "compromised_credentials": False,
                "risk_signal":             False
            }

        if resp.status_code == 429:
            logger.warning("[XposedOrNot] Rate limited")
            return {"source": "xposedornot", "error": "rate_limited", "risk_signal": False}

        resp.raise_for_status()
        data = resp.json()

        # XposedOrNot can return:
        #   {"breaches": [...]}  — dict with key
        #   [{"breachID": ...}]  — bare list
        #   {"BreachMetaData": [...]}  — alternate key
        if isinstance(data, list):
            breaches = data
        elif isinstance(data, dict):
            breaches = (
                data.get("breaches") or
                data.get("BreachMetaData") or
                data.get("breach_data") or
                []
            )
        else:
            breaches = []

        # Each breach item can be dict or plain string
        def _breach_name(b):
            if isinstance(b, dict):
                return b.get("breachID") or b.get("domain") or b.get("name") or ""
            return str(b)

        breach_names = [_breach_name(b) for b in breaches if b]

        high_risk_sources = ["linkedin", "collection1", "exploit", "antipublic",
                             "verification", "adobe", "canva", "dropbox"]
        has_high_risk = any(
            any(h in name.lower() for h in high_risk_sources)
            for name in breach_names
        )

        risk_signal = len(breaches) >= 2 or has_high_risk
        logger.info(f"[XposedOrNot] {email}: {len(breaches)} breaches | risk={risk_signal}")

        return {
            "source":                  "xposedornot",
            "entity_id":               entity_id,
            "email":                   email,
            "breach_count":            len(breaches),
            "breach_names":            breach_names[:5],
            "has_high_risk_breach":    has_high_risk,
            "compromised_credentials": len(breaches) > 0,
            "risk_signal":             risk_signal
        }

    except Exception as e:
        logger.error(f"[XposedOrNot] Error: {e}")
        return {"source": "xposedornot", "error": str(e), "risk_signal": False}


# ─────────────────────────────────────────────────────────────
# 3. Disify — Disposable email check
#    100% Free, NO key needed | https://www.disify.com
#    API: https://www.disify.com/api/email/{email}
# ─────────────────────────────────────────────────────────────
async def check_email_fraud_score(email: str, entity_id: str) -> Dict[str, Any]:
    """Check if email is disposable using Disify (free, no key needed)."""

    if not email or "@" not in email:
        return {
            "source":      "disify",
            "skipped":     True,
            "reason":      "no valid email provided",
            "risk_signal": False
        }

    url = f"https://www.disify.com/api/email/{email}"

    try:
        async with providers.async_client(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "FraudAnalyticsPlatform/1.0"})
            resp.raise_for_status()
            data = resp.json()

        # Disify response: {"format": true, "dns": true, "disposable": false, "alias": false}
        disposable  = data.get("disposable", False)
        valid_format = data.get("format", True)
        valid_dns    = data.get("dns", True)
        is_alias     = data.get("alias", False)

        # Risk: disposable OR invalid DNS (domain doesn't exist)
        risk_signal = disposable or not valid_dns

        logger.info(f"[Disify] {email}: disposable={disposable} dns={valid_dns} alias={is_alias} risk={risk_signal}")

        return {
            "source":       "disify",
            "entity_id":    entity_id,
            "email":        email,
            "domain":       email.split("@")[1].lower(),
            "disposable":   disposable,
            "valid_format": valid_format,
            "valid_dns":    valid_dns,
            "is_alias":     is_alias,
            "fraud_score":  0.9 if disposable else (0.5 if not valid_dns else 0.1),
            "risk_signal":  risk_signal
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("[Disify] Rate limited")
        logger.error(f"[Disify] HTTP error: {e}")
        return {"source": "disify", "error": str(e), "risk_signal": False}
    except Exception as e:
        logger.error(f"[Disify] Error: {e}")
        return {"source": "disify", "error": str(e), "risk_signal": False}
    except Exception as e:
        logger.error(f"[AbstractAPI] Error: {e}")
        return {"source": "abstract_email", "error": str(e), "risk_signal": False}


# ─────────────────────────────────────────────────────────────
# WebScraperAgent — orchestrates all 4 APIs
# ─────────────────────────────────────────────────────────────
class WebScraperAgent:

    SYSTEM_PROMPT = """You are a financial fraud intelligence specialist.
Analyze the API results from news, breach, and email fraud checks.
Synthesize a structured risk assessment. Focus only on genuine signals — avoid false positives.
Be precise: only flag risks that are clearly evidenced in the provided data."""

    def __init__(self, llm):
        self.llm = llm

    async def run(self, entity_id: str, entity_type: str,
                  entity_name: str = "", email: str = "") -> Dict[str, Any]:

        logger.info(f"[WebScraperAgent] Running for {entity_type}:{entity_id}")

        # If entity_name/email not passed, try to look them up from DB
        if not entity_name or not email:
            entity_name, email = await self._lookup_entity_info(entity_id)

        # ── Run all 3 APIs in parallel ────────────────────────
        serp_task   = fetch_serp_intelligence(entity_name, entity_id)
        breach_task = check_breach_exposure(email, entity_id)
        email_task  = check_email_fraud_score(email, entity_id)

        serp_data, breach_data, email_data = await asyncio.gather(
            serp_task, breach_task, email_task, return_exceptions=True
        )

        def safe(r): return r if not isinstance(r, Exception) else {'error': str(r), 'risk_signal': False}
        serp_data   = safe(serp_data)
        breach_data = safe(breach_data)
        email_data  = safe(email_data)

        signals, risk_score_components = [], []

        if serp_data.get('risk_signal'):
            neg = [m for m in serp_data.get('mentions', []) if m.get('sentiment') == 'negative']
            signals.append({'type': 'adverse_google_results', 'severity': 'HIGH',
                'detail': str(serp_data.get('negative_mentions', 0)) + ' negative Google results: '
                          + ' | '.join(m['headline'] for m in neg[:2])})
            risk_score_components.append(0.35)

        if breach_data.get('risk_signal'):
            signals.append({'type': 'data_breach_exposure', 'severity': 'MEDIUM',
                'detail': f"Found in {breach_data.get('breach_count', 0)} breach databases"})
            risk_score_components.append(0.15)

        if email_data.get('risk_signal'):
            signals.append({'type': 'disposable_email', 'severity': 'HIGH',
                'detail': f"Email fraud score: {email_data.get('fraud_score', 'N/A')} | disposable={email_data.get('disposable', True)}"})
            risk_score_components.append(0.20)

        web_risk_score = min(sum(risk_score_components), 1.0)

        # Build Google search context for LLM
        ctx_parts = []
        for i, m in enumerate(serp_data.get('mentions', []), 1):
            if m.get('sentiment') == 'negative':
                ctx_parts.append(
                    f"Result {i} [query: {m.get('search_query','')}]\n"
                    f"  Title:   {m.get('headline','')}\n"
                    f"  Snippet: {m.get('snippet','')}\n"
                    f"  Source:  {m.get('source','')} | {m.get('url','')}"
                )
            else:
                ctx_parts.append(f"[CONTEXT] {m.get('headline','')} — {m.get('snippet','')[:200]}")
        search_context = '\n'.join(ctx_parts) if ctx_parts else 'No relevant results found.'

        synthesis_prompt = (
            f'Entity: {entity_id} ({entity_type}) — Name: {entity_name}, Email: {email}\n\n'
            f'GOOGLE SEARCH RESULTS (3 targeted fraud/legal queries):\n{search_context}\n\n'
            f'OTHER CHECKS:\n- Data Breach: {breach_data}\n- Email Fraud: {email_data}\n\n'
            f'Detected Signals: {signals}\nWeb Risk Score: {web_risk_score:.2f}\n\n'
            'Synthesize into a structured fraud risk assessment.\n'
            'For news_narrative write a detailed paragraph using the Google snippets:\n'
            '  - What specific fraud/crime is alleged?\n'
            '  - Which institutions, banks, counterparties mentioned?\n'
            '  - Legal proceedings, court cases, arrest warrants?\n'
            '  - Amounts involved? Timeline of events?\n'
            'Be specific and factual. Only use what is in the search results.'
        )
        llm_synthesis: Optional[WebIntelligenceSynthesis] = None
        try:
            structured_llm = self.llm.with_structured_output(WebIntelligenceSynthesis)
            llm_synthesis = await structured_llm.ainvoke([
                SystemMessage(content=self.SYSTEM_PROMPT),
                HumanMessage(content=synthesis_prompt)
            ])
        except Exception as e:
            logger.warning(f"[WebScraperAgent] Structured LLM failed: {e}")
            # Build fallback news narrative from raw article data
            neg_articles = [m for m in serp_data.get("mentions", []) if m.get("sentiment") == "negative"]
            fallback_narrative = " | ".join(
                f"[{m.get('date','')}] {m.get('headline','')}" for m in neg_articles[:3]
            ) if neg_articles else "No adverse news found."
            llm_synthesis = WebIntelligenceSynthesis(
                summary=f"Web scan complete. {len(signals)} signal(s) detected.",
                news_narrative=fallback_narrative,
                key_risks=[s["detail"] for s in signals],
                confidence_score=round(web_risk_score, 2),
                recommended_action="Review signals manually."
            )

        logger.info(f"[WebScraperAgent] {entity_id}: web_risk={web_risk_score:.2f} signals={len(signals)}")

        return {
            "entity_id":          entity_id,
            "entity_name":        entity_name,
            "signals":            signals,
            "signal_count":       len(signals),
            "news":        serp_data,
            "breach":      breach_data,
            "email_fraud": email_data,
            "web_risk_score":     web_risk_score,
            # Structured LLM output fields
            "summary":            llm_synthesis.summary,
            "news_narrative":     llm_synthesis.news_narrative,
            "key_risks":          llm_synthesis.key_risks,
            "confidence_score":   llm_synthesis.confidence_score,
            "recommended_action": llm_synthesis.recommended_action,
            "timestamp":          datetime.utcnow().isoformat() + "Z"
        }

    async def _lookup_entity_info(self, entity_id: str):
        """Try to get entity name + email from PostgreSQL."""
        try:
            
            db_url  = os.getenv("DATABASE_URL", "postgresql://fraud_user:password@localhost:5432/fraud_db")
            url     = db_url.replace("postgresql://","").replace("postgresql+asyncpg://","")
            up, rest = url.split("@")
            user, pwd = up.split(":")
            hp, db    = rest.split("/")
            host, port = (hp.split(":") + ["5432"])[:2]

            conn = psycopg2.connect(host=host, port=int(port), dbname=db, user=user, password=pwd)
            cur  = conn.cursor()
            cur.execute("SELECT name, email FROM customers WHERE customer_id = %s", (entity_id,))
            row  = cur.fetchone()
            cur.close(); conn.close()

            if row:
                logger.info(f"[WebScraperAgent] Resolved {entity_id} → name={row[0]} email={row[1]}")
                return row[0], row[1]
        except Exception as e:
            logger.warning(f"[WebScraperAgent] DB lookup failed for {entity_id}: {e}")

        return entity_id, ""  # fallback