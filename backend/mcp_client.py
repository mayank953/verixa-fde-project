"""
MCP Fraud Analytics Client
===========================
Usage:
    python mcp_client.py --entity CUST_0042
    python mcp_client.py --entity CUST_0001 --scope quick
    python mcp_client.py --entity CUST_0042 --report

LangGraph internally decides:
  - Which agents to run
  - Parallel or sequential
  - Quick exit or full analysis
"""

import asyncio
import argparse
import json
import sys
from typing import Any, Dict
from mcp_server.server import call_tool

async def investigate(entity_id: str, scope: str = "full", report: bool = False):
    sys.path.insert(0, ".")
    

    tool = "generate_fraud_report" if report else "investigate_entity"
    args = {
        "entity_id":   entity_id,
        "entity_type": "customer",
        "scope":       scope,
        "format":      "markdown"
    }

    result_raw = await call_tool(tool, args)

    for content in result_raw:
        if hasattr(content, "text"):
            try:
                pretty_print(json.loads(content.text), report)
            except json.JSONDecodeError:
                print(content.text)


def pretty_print(result: Dict, show_report: bool = False):
    COLORS = {
        "LOW":      "\033[92m",
        "MEDIUM":   "\033[93m",
        "HIGH":     "\033[33m",
        "CRITICAL": "\033[91m"
    }
    RESET = "\033[0m"
    BOLD  = "\033[1m"
    CYAN  = "\033[96m"

    level = result.get("risk_level", "UNKNOWN")
    score = result.get("risk_score", 0)
    col   = COLORS.get(level, "")

    print(f"\n{BOLD}{CYAN}{'═'*52}{RESET}")
    print(f"{BOLD} FRAUD INVESTIGATION RESULT{RESET}")
    print(f"{CYAN}{'═'*52}{RESET}")
    print(f" Entity     : {CYAN}{BOLD}{result.get('entity_id')}{RESET}")
    print(f" Risk Score : {col}{BOLD}{score}{RESET}")
    print(f" Risk Level : {col}{BOLD}{level}{RESET}")
    print(f" SAR Filing : {'🚨 REQUIRED' if result.get('file_sar') else '✅ Not Required'}")

    # Show how LangGraph routed
    routing = result.get("routing_decision", "")
    steps   = result.get("completed_steps", [])
    if routing:
        if "quick" in routing:   label = "⚡ Quick Exit (SQL only)"
        elif "graph" in routing: label = "🕸  Graph Only"
        elif "sql"   in routing: label = "🗄  SQL Only"
        else:                    label = "🔍 Full Analysis (Web + SQL + KG parallel)"
        print(f"\n LangGraph  : {label}")
    if steps:
        print(f" Flow       : {' → '.join(steps)}")

    # Fraud indicators
    indicators = result.get("fraud_indicators", [])
    if indicators:
        print(f"\n{BOLD} Fraud Indicators ({len(indicators)}):{RESET}")
        for i in indicators:
            print(f"   \033[91m▸{RESET} {i}")

    # Recommendations
    recs = result.get("recommendations", [])
    if recs:
        print(f"\n{BOLD} Recommendations:{RESET}")
        for r in recs:
            print(f"   \033[92m✓{RESET} {r}")

    # Full report if requested
    if show_report and result.get("report"):
        print(f"\n{BOLD}{'─'*52}")
        print(" FULL REPORT")
        print(f"{'─'*52}{RESET}")
        print(result["report"])

    # Errors if any
    errors = result.get("errors", [])
    if errors:
        print(f"\n\033[91m{BOLD} Errors:{RESET}")
        for e in errors:
            print(f"   ⚠ {e}")

    print(f"\n{CYAN}{'═'*52}{RESET}\n")


async def main():
    parser = argparse.ArgumentParser(
        description="Fraud Analytics Client — powered by LangGraph"
    )
    parser.add_argument(
        "--entity", default="CUST_0300",
        help="Entity ID to investigate (e.g. CUST_0042)"
    )
    parser.add_argument(
        "--scope", default="full", choices=["full", "quick"],
        help="full = Web+SQL+KG | quick = SQL only (faster)"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Include full markdown report in output"
    )
    args = parser.parse_args()

    await investigate(args.entity, args.scope, args.report)


if __name__ == "__main__":
    asyncio.run(main())