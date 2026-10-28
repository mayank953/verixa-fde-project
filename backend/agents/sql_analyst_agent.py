"""
SQL Analyst Agent
=================
Executes intelligent SQL queries against an on-premise RDBMS
to detect transaction pattern anomalies.

Databases supported: PostgreSQL, MySQL, SQL Server, Oracle
Uses LangChain's SQL toolkit + custom fraud-specific queries.
"""

from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime
import ast, re
from backend.logger import GLOBAL_LOGGER as logger

from backend.models.model import SQLAnalysisSynthesis
from dotenv import load_dotenv
load_dotenv()


# ─────────────────────────────────────────────
# Pre-defined fraud detection SQL templates
# ─────────────────────────────────────────────
# ── DB Schema — given to LLM for self-healing query repair ───
DB_SCHEMA = """
TABLE: customers
  customer_id   VARCHAR(20) PRIMARY KEY
  name          VARCHAR(100)
  email         VARCHAR(100)
  phone         VARCHAR(20)
  home_country  CHAR(2)
  risk_level    VARCHAR(10)   -- LOW / MEDIUM / HIGH / CRITICAL
  risk_score    DECIMAL(4,3)
  is_flagged    BOOLEAN
  city          VARCHAR(50)
  occupation    VARCHAR(50)
  monthly_income DECIMAL(12,2)
  created_at    TIMESTAMP

TABLE: transactions
  txn_id        SERIAL PRIMARY KEY
  customer_id   VARCHAR(20)   FK → customers.customer_id
  merchant_id   VARCHAR(20)   FK → merchants.merchant_id
  amount        DECIMAL(12,2)
  currency      CHAR(3)
  country_code  CHAR(2)
  channel       VARCHAR(20)
  device_id     VARCHAR(30)
  ip_address    VARCHAR(20)
  txn_timestamp TIMESTAMP
  status        VARCHAR(20)
  txn_type      VARCHAR(20)

TABLE: merchants
  merchant_id   VARCHAR(20) PRIMARY KEY
  merchant_name VARCHAR(100)
  risk_category VARCHAR(10)   -- LOW / MEDIUM / HIGH / CRITICAL / BLACKLISTED
  country_code  CHAR(2)
  category      VARCHAR(50)

TABLE: chargebacks
  chargeback_id SERIAL PRIMARY KEY
  customer_id   VARCHAR(20)   FK → customers.customer_id
  txn_id        INTEGER       FK → transactions.txn_id
  amount        DECIMAL(12,2)
  reason        VARCHAR(100)
  status        VARCHAR(20)
  created_at    TIMESTAMP

RULES:
- Always qualify ambiguous columns with table alias (c.customer_id not customer_id)
- Use :entity_id as the parameter placeholder for customer_id filter
- Do NOT use INTERVAL filters — scan full history
- Use LEFT JOIN when joining transactions so customers with 0 txns still appear
"""


FRAUD_SQL_QUERIES = {
    "velocity_check": """
        SELECT 
            customer_id,
            COUNT(*) as txn_count,
            SUM(amount) as total_amount,
            COUNT(DISTINCT merchant_id) as unique_merchants,
            MAX(amount) as max_single_txn,
            MIN(txn_timestamp) as first_txn,
            MAX(txn_timestamp) as last_txn,
            EXTRACT(HOUR FROM MAX(txn_timestamp) - MIN(txn_timestamp)) as span_hours
        FROM transactions
        WHERE customer_id = :entity_id
          -- no time filter: scan full history
        GROUP BY customer_id
    """,

    "round_amount_detection": """
        SELECT 
            COUNT(*) as round_txn_count,
            COUNT(*) * 100.0 / (SELECT COUNT(*) FROM transactions WHERE customer_id = :entity_id) as round_pct
        FROM transactions
        WHERE customer_id = :entity_id
          AND amount % 1000 = 0
          AND amount > 0
    """,

    "geographic_anomaly": """
        SELECT 
            t.customer_id,
            COUNT(DISTINCT t.country_code) as country_count,
            STRING_AGG(DISTINCT t.country_code, ', ') as countries,
            COUNT(*) FILTER (WHERE t.country_code != c.home_country) as foreign_txns
        FROM transactions t
        JOIN customers c ON t.customer_id = c.customer_id
        WHERE t.customer_id = :entity_id
          -- no time filter: scan full history
        GROUP BY t.customer_id, c.home_country
    """,

    "structuring_detection": """
        -- Structuring: many txns just below the reporting threshold of the customer's jurisdiction
        -- (the band comes from the jurisdiction_rules table, so the threshold is a setting, not a constant)
        SELECT
            DATE(t.txn_timestamp) as txn_date,
            COUNT(*) as daily_count,
            SUM(t.amount) as daily_total,
            COUNT(*) FILTER (WHERE t.amount BETWEEN r.structuring_low AND r.structuring_high) as near_threshold_count
        FROM transactions t
        JOIN customers c ON c.customer_id = t.customer_id
        JOIN jurisdiction_rules r ON r.country_code = c.home_country
        WHERE t.customer_id = :entity_id
          -- no time filter: scan full history
        GROUP BY DATE(t.txn_timestamp), r.structuring_low, r.structuring_high, r.structuring_min_count
        HAVING COUNT(*) FILTER (WHERE t.amount BETWEEN r.structuring_low AND r.structuring_high) >= r.structuring_min_count
        ORDER BY near_threshold_count DESC
    """,

    "counterparty_risk": """
        SELECT 
            t.merchant_id,
            m.merchant_name,
            m.risk_category,
            COUNT(*) as txn_count,
            SUM(t.amount) as total_exposure
        FROM transactions t
        JOIN merchants m ON t.merchant_id = m.merchant_id
        WHERE t.customer_id = :entity_id
          AND m.risk_category IN ('HIGH', 'CRITICAL', 'BLACKLISTED')
        GROUP BY t.merchant_id, m.merchant_name, m.risk_category
        ORDER BY total_exposure DESC
    """,

    "chargeback_history": """
        SELECT 
            COUNT(*) as total_chargebacks,
            SUM(amount) as total_chargeback_value,
            COUNT(*) * 100.0 / NULLIF(
                (SELECT COUNT(*) FROM transactions WHERE customer_id = :entity_id), 0
            ) as chargeback_rate
        FROM chargebacks
        WHERE customer_id = :entity_id
          -- no time filter: scan full history
    """,

    "customer_risk_profile": """
        SELECT
            c.customer_id,
            c.name,
            c.risk_level,
            c.risk_score         AS db_risk_score,
            c.is_flagged,
            c.occupation,
            c.home_country,
            COUNT(t.txn_id)          AS lifetime_txn_count,
            COALESCE(SUM(t.amount),0) AS lifetime_amount,
            COUNT(DISTINCT t.country_code) AS countries_used,
            COUNT(DISTINCT t.device_id)    AS devices_used
        FROM customers c
        LEFT JOIN transactions t ON c.customer_id = t.customer_id
        WHERE c.customer_id = :entity_id
        GROUP BY c.customer_id, c.name, c.risk_level,
                 c.risk_score, c.is_flagged, c.occupation, c.home_country
    """
}


class SQLAnalystAgent:
    """Agent that executes targeted SQL queries for fraud pattern detection."""

    SYSTEM_PROMPT = """You are a financial fraud SQL analyst.
Given raw SQL query results, identify transaction patterns that indicate fraud:
- Velocity fraud (too many transactions in short time)
- Structuring (transactions just below reporting limits)
- Geographic anomalies (impossible travel)
- Round-amount patterns
- High-risk counterparty exposure

Be precise and concise. Only flag anomalies clearly present in the data."""

    def __init__(self, llm, db_url: Optional[str] = None):
        self.llm = llm
        self.db_url = db_url or os.getenv("DATABASE_URL")
        self._db: Optional[SQLDatabase] = None

    def _get_db(self) -> SQLDatabase:
        if self._db is None:
            try:
                self._db = SQLDatabase.from_uri(self.db_url, sample_rows_in_table_info=0)
                logger.info("PostgreSQL connection established")
            except Exception as e:
                logger.error(f"PostgreSQL connection failed: {type(e).__name__}: {e}")
                raise ConnectionError(
                    f"PostgreSQL unavailable at {self.db_url}. "
                    f"Ensure DATABASE_URL is set correctly and the database is running. "
                    f"Reason: {type(e).__name__}: {e}"
                )
        return self._db

    def _build_query(self, query_template: str, entity_id: str) -> str:
        """Strip comments and inject entity_id parameter."""
        lines = [l for l in query_template.strip().splitlines()
                 if not l.strip().startswith("--")]
        return "\n".join(lines).replace(":entity_id", f"'{entity_id}'").strip()

    def _parse_result(self, raw: str) -> List:
        """
        Parse db.run() string result into Python list.
        Handles PostgreSQL types that ast.literal_eval cannot parse:
          - Decimal('123.45')     → float
          - datetime.datetime(…)  → ISO string
          - datetime.date(…)      → ISO string
          - None                  → None
        ALWAYS returns a list — never raises, never returns a raw string.
        """
        

        if not raw or raw.strip() in ("", "[]", "None"):
            return []

        cleaned = raw.strip()

        # Replace Decimal('…') → float literal
        cleaned = re.sub(
            r"Decimal\('([^']+)'\)",
            lambda m: str(float(m.group(1))),
            cleaned
        )

        # Replace datetime.datetime(y,mo,d,h,mi,s,us) → 'YYYY-MM-DD HH:MM:SS'
        cleaned = re.sub(
            r"datetime\.datetime\((\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*\d+)?\)",
            lambda m: f"'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d} "
                      f"{int(m.group(4)):02d}:{int(m.group(5)):02d}:{int(m.group(6)):02d}'",
            cleaned
        )

        # Replace datetime.date(y,mo,d) → 'YYYY-MM-DD'
        cleaned = re.sub(
            r"datetime\.date\((\d+),\s*(\d+),\s*(\d+)\)",
            lambda m: f"'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'",
            cleaned
        )

        try:
            result = ast.literal_eval(cleaned)
            if isinstance(result, list):  return result
            if isinstance(result, tuple): return [result]
            if isinstance(result, dict):  return [result]
            return []
        except Exception as e:
            logger.warning(f"[SQLAgent] Could not parse query result: {e} | raw={raw[:200]}")
            return []

    async def _execute_with_llm_repair(
        self,
        query_name: str,
        query_template: str,
        entity_id: str,
        max_retries: int = 2
    ) -> Any:
        """
        Execute a SQL query with LLM self-healing retry.
        On failure: LLM gets original query + error + full DB schema
        → generates corrected query → retried automatically.
        Up to max_retries repair attempts before giving up.
        """
        db            = self._get_db()
        current_query = self._build_query(query_template, entity_id)
        last_error    = None

        if not current_query:
            raise ValueError(f"SQL query '{query_name}' is empty after stripping comments")

        for attempt in range(1 + max_retries):
            try:
                raw    = db.run(current_query)
                result = self._parse_result(raw)
                if attempt > 0:
                    logger.info(f"[SQLAgent] '{query_name}' repaired on attempt {attempt + 1} ✅")
                return result

            except Exception as e:
                last_error = e
                if attempt >= max_retries:
                    break

                logger.warning(
                    f"[SQLAgent] '{query_name}' attempt {attempt + 1} failed: "
                    f"{type(e).__name__}: {e} — asking LLM to repair..."
                )

                # ── LLM self-repair ────────────────────────────
                repair_prompt = f"""You are a PostgreSQL expert. Fix the broken SQL query below.

ERROR:
{type(e).__name__}: {e}

BROKEN QUERY:
{current_query}

DATABASE SCHEMA:
{DB_SCHEMA}

Return ONLY the corrected SQL — no explanation, no markdown, no backticks.
The value '{entity_id}' is already injected — do NOT use :entity_id placeholder.
"""
                try:
                    repair_resp = await self.llm.ainvoke(
                        [HumanMessage(content=repair_prompt)]
                    )
                    repaired = repair_resp.content.strip()
                    # Strip markdown fences if LLM added them
                    if "```" in repaired:
                        repaired = "\n".join(
                            line for line in repaired.splitlines()
                            if not line.strip().startswith("```")
                        ).strip()
                    current_query = repaired
                    logger.info(
                        f"[SQLAgent] LLM repaired '{query_name}':\n"
                        f"{current_query[:300]}{'...' if len(current_query) > 300 else ''}"
                    )
                except Exception as repair_err:
                    logger.error(f"[SQLAgent] LLM repair call failed: {repair_err}")
                    break

        logger.error(
            f"[SQLAgent] '{query_name}' permanently failed "
            f"after {max_retries + 1} attempts: {last_error}"
        )
        raise last_error

    def _execute_query(self, query_template: str, entity_id: str) -> Any:
        """Sync fallback — use _execute_with_llm_repair for async runs."""
        db    = self._get_db()
        query = self._build_query(query_template, entity_id)
        if not query:
            raise ValueError("SQL query is empty after stripping comments")
        raw = db.run(query)
        return self._parse_result(raw)

    async def run(self, entity_id: str, entity_type: str, web_context: Dict) -> Dict[str, Any]:
        logger.info(f"SQLAnalystAgent running for {entity_type}:{entity_id}")

        # Execute all fraud detection queries — with LLM self-healing retry on failure
        query_results = {}
        for query_name, query_template in FRAUD_SQL_QUERIES.items():
            try:
                result = await self._execute_with_llm_repair(
                    query_name, query_template, entity_id
                )
                query_results[query_name] = result
            except Exception as e:
                logger.error(f"[SQLAgent] '{query_name}' permanently failed: {e}")
                query_results[query_name] = []   # empty — don't crash entire pipeline

        # Derive anomaly flags
        # Helper: extract value from tuple list or dict
        def _val(result, idx_or_key, default=0):
            if isinstance(result, list) and result:
                row = result[0]
                if isinstance(row, (list, tuple)):
                    try: return row[idx_or_key] if isinstance(idx_or_key, int) else default
                    except: return default
                if isinstance(row, dict):
                    return row.get(idx_or_key, default)
            if isinstance(result, dict):
                return result.get(idx_or_key, default)
            return default

        # ── Rich anomaly indicators with actual data ─────────────
        # Each entry: {"flag": "CODE", "description": "Human readable with numbers"}
        anomaly_details = []

        velocity   = query_results.get("velocity_check", [])
        txn_count  = _val(velocity, 1, 0)
        total_amt  = _val(velocity, 2, 0)
        max_single = _val(velocity, 4, 0)
        span_hours = _val(velocity, 7, 999)

        if txn_count > 20:
            anomaly_details.append({
                "flag": "HIGH_VELOCITY",
                "description": (
                    f"Unusually high transaction volume detected — {int(txn_count)} transactions "
                    f"totalling ${float(total_amt):,.0f}, with a maximum single transaction of "
                    f"${float(max_single):,.0f}. High velocity is a strong indicator of automated "
                    f"fraud or coordinated money movement."
                )
            })
        if span_hours < 24 and txn_count > 15:
            anomaly_details.append({
                "flag": "BURST_TRANSACTIONS",
                "description": (
                    f"Burst activity pattern — {int(txn_count)} transactions compressed within "
                    f"{int(span_hours)} hours. Rapid-fire transactions within a narrow window "
                    f"are a hallmark of account takeover and card testing fraud."
                )
            })

        round_amt = query_results.get("round_amount_detection", [])
        round_pct = float(_val(round_amt, 1, 0) or 0)
        if round_pct > 20:
            anomaly_details.append({
                "flag": "ROUND_AMOUNT_PATTERN",
                "description": (
                    f"{round_pct:.1f}% of transactions are round-number amounts. "
                    f"Round amounts (e.g. $1,000, $5,000) are commonly used in "
                    f"structuring schemes to avoid automated detection thresholds."
                )
            })

        geo           = query_results.get("geographic_anomaly", [])
        country_count = int(_val(geo, 1, 0) or 0)
        countries     = _val(geo, 2, "") or ""
        foreign_txns  = int(_val(geo, 3, 0) or 0)
        if country_count > 2:
            anomaly_details.append({
                "flag": "GEOGRAPHIC_ANOMALY",
                "description": (
                    f"Transactions span {country_count} countries ({countries}), "
                    f"with {foreign_txns} foreign transactions. Multi-jurisdiction "
                    f"activity — especially involving high-risk jurisdictions — is a "
                    f"key indicator of cross-border money laundering and sanctions evasion."
                )
            })

        structuring = query_results.get("structuring_detection", [])
        if isinstance(structuring, list) and len(structuring) > 0:
            near_thresh = sum(int(_val([r], 3, 0) or 0) for r in structuring)
            anomaly_details.append({
                "flag": "STRUCTURING_PATTERN",
                "description": (
                    f"Structuring (smurfing) pattern detected across {len(structuring)} days — "
                    f"{near_thresh} transactions found just below the jurisdiction's reporting threshold. "
                    f"This is a deliberate tactic to avoid Currency Transaction Report (CTR) filing "
                    f"requirements and is a federal financial crime."
                )
            })

        counterparty = query_results.get("counterparty_risk", [])
        if isinstance(counterparty, list) and len(counterparty) > 0:
            cp_names    = ", ".join(_val([r], 1, "") or _val([r], 0, "") for r in counterparty[:3])
            cp_exposure = sum(float(_val([r], 4, 0) or 0) for r in counterparty)
            anomaly_details.append({
                "flag": "HIGH_RISK_COUNTERPARTIES",
                "description": (
                    f"Transactions with {len(counterparty)} high-risk or blacklisted merchants: "
                    f"{cp_names}. Total exposure: ${cp_exposure:,.0f}. "
                    f"Dealing with high-risk counterparties significantly elevates AML risk "
                    f"and may indicate complicit merchant fraud."
                )
            })

        chargeback      = query_results.get("chargeback_history", [])
        chargeback_rate = float(_val(chargeback, 2, 0) or 0)
        chargeback_val  = float(_val(chargeback, 1, 0) or 0)
        if chargeback_rate > 5:
            anomaly_details.append({
                "flag": "HIGH_CHARGEBACK_RATE",
                "description": (
                    f"Chargeback rate of {chargeback_rate:.1f}% — total disputed amount "
                    f"${chargeback_val:,.0f}. Industry threshold is <1%. Elevated chargebacks "
                    f"indicate either a fraudulent merchant relationship, friendly fraud, "
                    f"or systematic account abuse."
                )
            })

        # Keep simple flag list for backward compat + risk scoring
        anomaly_flags = [d["flag"] for d in anomaly_details]

        # ── LLM structured synthesis ──────────────────────────
        # Build human-readable anomaly summary for LLM
        anomaly_descriptions = "\n".join(
            f"- [{d['flag']}] {d['description']}" for d in anomaly_details
        ) if anomaly_details else "No anomalies detected."

        synthesis_prompt = f"""
Entity: {entity_id} ({entity_type})
Query Results Summary:
- Total transactions: {int(txn_count)}, Total amount: ${float(total_amt):,.0f}
- Countries: {country_count}, Foreign txns: {foreign_txns}
- Chargeback rate: {chargeback_rate:.1f}%

Detected Anomalies:
{anomaly_descriptions}

Web Context Available: {bool(web_context)}

Analyse the transaction patterns and provide a structured fraud assessment.
Be specific — reference actual numbers from the anomalies above.
Only report findings clearly supported by the data.
"""
        sql_synthesis: Optional[SQLAnalysisSynthesis] = None
        try:
            structured_llm = self.llm.with_structured_output(SQLAnalysisSynthesis)
            sql_synthesis = await structured_llm.ainvoke([
                SystemMessage(content=self.SYSTEM_PROMPT),
                HumanMessage(content=synthesis_prompt)
            ])
        except Exception as e:
            logger.warning(f"[SQLAnalystAgent] Structured LLM failed: {e}")
            sql_synthesis = SQLAnalysisSynthesis(
                summary=f"SQL analysis complete. {len(anomaly_flags)} anomaly flag(s) detected.",
                severity="HIGH" if len(anomaly_flags) >= 3 else "MEDIUM" if anomaly_flags else "LOW",
                key_findings=anomaly_flags,
                recommended_escalation=len(anomaly_flags) >= 3
            )

        return {
            "entity_id":              entity_id,
            "query_results":          query_results,
            "anomaly_flags":          anomaly_flags,
            "anomaly_details":        anomaly_details,      # rich descriptions with actual numbers
            "transaction_count":      txn_count,
            "total_amount":           _val(velocity, 2, 0),
            "country_count":          country_count,
            "countries":              countries,
            "sql_risk_score":         min(len(anomaly_flags) * 0.15, 0.9),
            # Structured LLM output fields
            "summary":                sql_synthesis.summary,
            "severity":               sql_synthesis.severity,
            "key_findings":           sql_synthesis.key_findings,
            "recommended_escalation": sql_synthesis.recommended_escalation,
            "timestamp":              datetime.utcnow().isoformat() + "Z"
        }