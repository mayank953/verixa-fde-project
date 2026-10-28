from typing import Dict, Optional
import psycopg2
from fastapi import HTTPException
import os
from backend.mcp_server.server import call_tool
from backend.logger import GLOBAL_LOGGER as logger
import re,json


async def _mcp_investigate(
    entity_id:    str,
    scope:        str  = "full",
    user_role:    str  = "admin",
    user_id:      str  = "USR_001",
    db_schema:    str  = "admin",
    customer_info: dict = None,
) -> dict:
    """
    Route investigation through MCP server (call_tool).
    Passes user role for role-based agent selection.
    """
    result_raw = await call_tool("investigate_entity", {
        "entity_id":    entity_id,
        "entity_type":  "customer",
        "scope":        scope,
        "user_role":    user_role,
        "user_id":      user_id,
        "db_schema":    db_schema,
        "customer_info": customer_info or {},
    })
    for content in result_raw:
        if hasattr(content, "text"):
            return json.loads(content.text)
    return {}


# ══════════════════════════════════════════════════════════════
# DB HELPERS
# ══════════════════════════════════════════════════════════════

def _get_db_connection():
    """Parse DATABASE_URL and return a psycopg2 connection."""
    db_url = os.getenv("DATABASE_URL", "postgresql://fraud_user:password@localhost:5432/fraud_db")
    logger.info(f"Connecting to DB: {db_url}")

    pattern = (
        r"(?P<scheme>\w+)://"
        r"(?P<username>[^:]+):"
        r"(?P<password>.+)@"
        r"(?P<host>[^:]+):"
        r"(?P<port>\d+)/"
        r"(?P<database>.+)"
    )
    m = re.match(pattern, db_url)
    if not m:
        raise ValueError(f"Invalid DATABASE_URL: {db_url}")

    config = {**m.groupdict(), "port": int(m.group("port"))}
    return psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["database"],
        user=config["username"],
        password=config["password"],
    )


def validate_customer(entity_id: str) -> Optional[Dict]:
    """
    Check if a customer exists in the DB.
    Returns a customer dict if found, None otherwise.
    Raises HTTPException(500) on DB errors.
    """
    try:
        conn = _get_db_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT customer_id, name, email, risk_level, risk_score, is_flagged, occupation
            FROM customers
            WHERE customer_id = %s
            """,
            (entity_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return None

        return {
            "customer_id": row[0],
            "name":        row[1],
            "email":       row[2],
            "risk_level":  row[3],
            "risk_score":  float(row[4]) if row[4] else 0.0,
            "is_flagged":  row[5],
            "occupation":  row[6],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ══════════════════════════════════════════════════════════════
# SHARED DATA HELPERS
# ══════════════════════════════════════════════════════════════

def _flatten_indicators(indicators) -> list:
    """Normalise fraud indicator objects/strings into a flat list of dicts."""
    return [
        {
            "source":      i.get("source", ""),
            "flag":        i.get("flag", ""),
            "description": i.get("description", ""),
        } if isinstance(i, dict) else {"source": "", "flag": str(i), "description": str(i)}
        for i in (indicators or [])
    ]