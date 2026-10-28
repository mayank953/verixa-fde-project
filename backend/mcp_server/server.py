"""
MCP Server — Fraud Analytics
==============================
Har tool LangGraph orchestrator se guzarta hai.
Scope-based routing:
  - investigate_entity   → scope="full"    → Web + SQL + KG + Risk
  - get_risk_score       → scope="quick"   → SQL only (fast)
  - query_knowledge_graph→ scope="graph"   → KG only
  - run_sql_fraud_check  → scope="sql"     → SQL only (specific check)
  - generate_fraud_report→ scope="full"    → Web + SQL + KG + Risk + report
"""

from __future__ import annotations
import asyncio
import json
import os
from typing import Any, Dict
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from backend.logger import GLOBAL_LOGGER as logger


app    = Server("fraud-analytics-mcp")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="investigate_entity",
            description="Run full multi-agent LangGraph fraud investigation. Runs Web + SQL + KG agents in parallel, then synthesizes final risk score.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id":   {"type": "string"},
                    "entity_type": {"type": "string", "enum": ["customer","merchant","transaction"], "default": "customer"},
                    "scope":       {"type": "string", "enum": ["quick","full"], "default": "full"}
                },
                "required": ["entity_id"]
            }
        ),
        types.Tool(
            name="get_risk_score",
            description="Quick risk score via LangGraph (SQL-only path). Fast — skips Web and KG.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id":   {"type": "string"},
                    "entity_type": {"type": "string", "default": "customer"}
                },
                "required": ["entity_id"]
            }
        ),
        types.Tool(
            name="query_knowledge_graph",
            description="Query Neo4j via LangGraph KG node. Detects ring fraud, mule chains, shared devices.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id":  {"type": "string"},
                    "query_type": {
                        "type": "string",
                        "enum": ["neighbors","mule_chain","ring_fraud","shared_identity"]
                    }
                },
                "required": ["entity_id", "query_type"]
            }
        ),
        types.Tool(
            name="run_sql_fraud_check",
            description="Run a specific SQL fraud check via LangGraph SQL node.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id":  {"type": "string"},
                    "check_type": {
                        "type": "string",
                        "enum": ["velocity","structuring","geographic","counterparty","chargeback"]
                    }
                },
                "required": ["entity_id", "check_type"]
            }
        ),
        types.Tool(
            name="generate_fraud_report",
            description="Full investigation + formatted markdown report via LangGraph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id":   {"type": "string"},
                    "entity_type": {"type": "string", "default": "customer"},
                    "format":      {"type": "string", "enum": ["markdown","json"], "default": "markdown"}
                },
                "required": ["entity_id"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> list[types.TextContent]:

    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    # ── Single import — everything goes through LangGraph ────────
    from agents.orchestrator import investigate_entity as langgraph_invoke

    entity_id   = arguments.get("entity_id")
    entity_type = arguments.get("entity_type", "customer")

    # ── Tool → scope mapping ─────────────────────────────────────
    # orchestrator uses scope to decide which nodes run
    if name == "investigate_entity":
        scope         = arguments.get("scope", "full")
        user_role     = arguments.get("user_role", "admin")
        user_id       = arguments.get("user_id", "USR_001")
        db_schema     = arguments.get("db_schema", "admin")
        customer_info = arguments.get("customer_info", {})

        state = await langgraph_invoke(
            entity_id, entity_type, scope,
            user_role=user_role,
            user_id=user_id,
            db_schema=db_schema,
            customer_info=customer_info,
        )

        result = {
            "entity_id":           entity_id,
            "entity_type":         entity_type,
            "user_role":           user_role,
            "risk_score":          state.get("risk_score"),
            "risk_level":          state.get("risk_level"),
            "fraud_indicators":    state.get("fraud_indicators", []),
            "fraud_summary":       state.get("fraud_summary", ""),
            "recommendations":     state.get("recommendations", []),
            "file_sar":            state.get("file_sar", False),
            "monitoring_days":     state.get("monitoring_days", 30),
            "routing_decision":    state.get("routing_decision", ""),
            "completed_steps":     state.get("completed_steps", []),
            "errors":              state.get("errors", []),
            # Role-specific findings
            "linkedin_findings":   state.get("linkedin_findings"),
            "youtube_findings":    state.get("youtube_findings"),
            "itr_credit_findings": state.get("itr_credit_findings"),
            "income_findings":     state.get("income_findings"),
        }

    elif name == "get_risk_score":
        # scope=quick → LangGraph takes quick_exit path (SQL only)
        state = await langgraph_invoke(entity_id, entity_type, scope="quick")

        result = {
            "entity_id":        entity_id,
            "risk_score":       state.get("risk_score"),
            "risk_level":       state.get("risk_level"),
            "quick_flags":      state.get("sql_findings", {}).get("anomaly_flags", []),
            "routing_decision": state.get("routing_decision", ""),
            "completed_steps":  state.get("completed_steps", [])
        }

    elif name == "query_knowledge_graph":
        # scope=graph → LangGraph runs KG node only
        query_type = arguments.get("query_type", "neighbors")
        state = await langgraph_invoke(entity_id, entity_type, scope=f"graph:{query_type}")

        result = {
            "entity_id":         entity_id,
            "query_type":        query_type,
            "graph_insights":    state.get("graph_insights", {}),
            "fraud_patterns":    state.get("graph_insights", {}).get("fraud_patterns", []),
            "suspicious_clusters": state.get("graph_insights", {}).get("suspicious_clusters", 0),
            "completed_steps":   state.get("completed_steps", []),
            "errors":            state.get("errors", [])
        }

    elif name == "run_sql_fraud_check":
        # scope=sql:<check_type> → LangGraph runs SQL node only
        check_type = arguments.get("check_type", "velocity")
        state = await langgraph_invoke(entity_id, entity_type, scope=f"sql:{check_type}")

        sql_findings = state.get("sql_findings", {})
        result = {
            "entity_id":       entity_id,
            "check_type":      check_type,
            "anomaly_flags":   sql_findings.get("anomaly_flags", []),
            "flagged":         check_type.upper() in " ".join(sql_findings.get("anomaly_flags", [])),
            "query_results":   sql_findings.get("query_results", {}).get(
                               {"velocity": "velocity_check", "structuring": "structuring_detection",
                                "geographic": "geographic_anomaly", "counterparty": "counterparty_risk",
                                "chargeback": "chargeback_history"}.get(check_type, check_type), []
                               ),
            "completed_steps": state.get("completed_steps", []),
            "errors":          state.get("errors", [])
        }

    elif name == "generate_fraud_report":
        # Always full scope for report
        state = await langgraph_invoke(entity_id, entity_type, scope="full")

        result = {
            "entity_id":        entity_id,
            "format":           arguments.get("format", "markdown"),
            "risk_level":       state.get("risk_level"),
            "risk_score":       state.get("risk_score"),
            "fraud_indicators": state.get("fraud_indicators", []),
            "routing_decision": state.get("routing_decision", ""),
            "completed_steps":  state.get("completed_steps", []),
            "report":           state.get("report", "Report unavailable"),
            "errors":           state.get("errors", [])
        }

    else:
        result = {"error": f"Unknown tool: {name}"}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def main():
    logger.info("Starting Fraud Analytics MCP Server...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())