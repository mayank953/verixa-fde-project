"""
Fraud Analytics Multi-Agent Orchestrator — Real LangGraph
==========================================================
Proper LangGraph implementation with:
  - Conditional routing (risk-based branching)
  - Parallel execution (SQL + KG run simultaneously)
  - State-driven decisions at each node
  - Early exit for very low risk entities

Flow:
                    ┌─────────────────┐
                    │   web_scraping  │  (always runs first)
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  route_after_web│  conditional router
                    └──┬─────────┬───┘
                       │         │
              web_risk │         │ web_risk
               < 0.2   │         │  >= 0.2
                       │         │
              ┌────────▼──┐  ┌───▼──────────────────┐
              │ quick_exit│  │  parallel_analysis    │
              └────────┬──┘  │  (SQL + KG together) │
                       │     └───────────┬───────────┘
                       │                 │
                       └────────┬────────┘
                                │
                       ┌────────▼────────┐
                       │ risk_synthesis  │
                       └────────┬────────┘
                                │
                               END
"""

from __future__ import annotations
import os
import json
import asyncio
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from backend.agents.web_scraper_agent import WebScraperAgent
from backend.agents.sql_analyst_agent import SQLAnalystAgent
from backend.agents.knowledge_graph_agent import KnowledgeGraphAgent
from backend.agents.risk_synthesizer_agent import RiskSynthesizerAgent
from backend.agents.linkedin_agent import run_linkedin_agent
from backend.agents.youtube_agent import run_youtube_agent
from backend.agents.itr_credit_agent import run_itr_credit_agent
from backend.agents.income_agent import run_income_agent
from backend.auth.config import ROLE_AGENTS
from backend.logger import GLOBAL_LOGGER as logger

0

# ─────────────────────────────────────────────────────────────
# Shared State Schema
# ─────────────────────────────────────────────────────────────
class FraudAnalyticsState(TypedDict):
    messages:             Annotated[List[BaseMessage], add_messages]

    # Input
    entity_id:            str
    entity_type:          str
    investigation_scope:  str           # "full" | "quick"

    # Intermediate results
    web_intelligence:     Dict[str, Any]
    sql_findings:         Dict[str, Any]
    graph_insights:       Dict[str, Any]

    # Final output
    risk_score:           Optional[float]
    risk_level:           Optional[str]
    fraud_indicators:     List[str]
    recommendations:      List[str]
    report:               Optional[str]
    file_sar:             bool

    # New agent findings (role-based)
    linkedin_findings:    Optional[Dict[str, Any]]
    youtube_findings:     Optional[Dict[str, Any]]
    itr_credit_findings:  Optional[Dict[str, Any]]
    income_findings:      Optional[Dict[str, Any]]
    customer_info:        Optional[Dict[str, Any]]

    # User context
    user_role:            Optional[str]   # admin | reviewer1 | reviewer2
    user_id:              Optional[str]
    db_schema:            Optional[str]

    # Control
    current_agent:        str
    routing_decision:     str           # "quick_exit" | "full_analysis"
    errors:               List[str]
    completed_steps:      List[str] # RunLogger instance — not serialized by LangGraph


# ─────────────────────────────────────────────────────────────
# Initialize LLM + Agents (once, shared across nodes)
# ─────────────────────────────────────────────────────────────
def _make_llm():
    return ChatGoogleGenerativeAI(model="gemini-3.1-pro-preview", temperature=0,api_key=os.getenv("GOOGLE_API_KEY"))

llm         = _make_llm()
web_agent   = WebScraperAgent(llm=llm)
sql_agent   = SQLAnalystAgent(llm=llm)
kg_agent    = KnowledgeGraphAgent(llm=llm)
synth_agent = RiskSynthesizerAgent(llm=llm)


# ─────────────────────────────────────────────────────────────
# NODE 1 — Web Scraping (always runs)
# ─────────────────────────────────────────────────────────────
async def web_scraping_node(state: FraudAnalyticsState) -> Dict:
    logger.info(f"[Node:WebScraper] Starting for {state['entity_id']}")
   
    t0 = datetime.now()
    try:
        result = await web_agent.run(
            entity_id=state["entity_id"],
            entity_type=state["entity_type"]
        )
        web_risk = result.get("web_risk_score", 0.0)
        elapsed  = (datetime.now() - t0).total_seconds() * 1000
        logger.info(f"[Node:WebScraper] web_risk_score = {web_risk:.2f}")
        return {
            "web_intelligence": result,
            "current_agent":    "router",
            "completed_steps":  state["completed_steps"] + ["web_scraping"],
            "messages": [AIMessage(content=
                f"[WebScraper] web_risk={web_risk:.2f} | "
                f"signals={len(result.get('signals', []))}"
            )]
        }
    except Exception as e:
        logger.error(f"[Node:WebScraper] Error: {e}")

        return {
            "web_intelligence": {"web_risk_score": 0.0},
            "current_agent":    "router",
            "errors":           state["errors"] + [f"WebScraper: {e}"],
            "completed_steps":  state["completed_steps"] + ["web_scraping_failed"],
        }


# ─────────────────────────────────────────────────────────────
# CONDITIONAL ROUTER — after web scraping
# ─────────────────────────────────────────────────────────────
def route_after_web(state: FraudAnalyticsState) -> str:
    """
    Routing logic based on scope:
      scope = "quick"           → quick_exit  (SQL only, skip KG + Web)
      scope = "graph:<query>"   → graph_only  (KG only)
      scope = "sql:<check>"     → sql_only    (SQL only, specific check)
      scope = "full"            → parallel_analysis (SQL + KG together)
      web_risk < 0.2 + "quick"  → quick_exit
    """
    web_risk = state.get("web_intelligence", {}).get("web_risk_score", 0.0)
    scope    = state.get("investigation_scope", "full")

    if scope.startswith("graph:"):
        logger.info(f"[Router] scope={scope} → GRAPH ONLY")
        run_log = state.get("run_logger")
        if run_log: run_log.log_routing(scope, "graph_only", web_risk)
        return "graph_only"

    if scope.startswith("sql:"):
        logger.info(f"[Router] scope={scope} → SQL ONLY")
        run_log = state.get("run_logger")
        if run_log: run_log.log_routing(scope, "sql_only", web_risk)
        return "sql_only"

    if scope == "quick" or (web_risk < 0.2 and scope == "quick"):
        logger.info(f"[Router] web_risk={web_risk:.2f} + scope=quick → QUICK EXIT")
        run_log = state.get("run_logger")
        if run_log: run_log.log_routing(scope, "quick_exit", web_risk)
        return "quick_exit"

    logger.info(f"[Router] web_risk={web_risk:.2f} + scope={scope} → FULL ANALYSIS")
    run_log = state.get("run_logger")
    if run_log: run_log.log_routing(scope, "parallel_analysis", web_risk)
    return "parallel_analysis"


# ─────────────────────────────────────────────────────────────
# NODE 2A — Quick Exit (low risk, quick scope)
# ─────────────────────────────────────────────────────────────
async def graph_only_node(state: FraudAnalyticsState) -> Dict:
    """Run KG agent only — for query_knowledge_graph MCP tool."""
    scope      = state.get("investigation_scope", "")
    query_type = scope.split(":")[1] if ":" in scope else "entity_neighborhood"
    logger.info(f"[Node:GraphOnly] Running KG query: {query_type} for {state['entity_id']}")

    t0 = datetime.now()
    try:
        result = await kg_agent.run(
            entity_id=state["entity_id"],
            entity_type=state["entity_type"],
            sql_findings={},
            web_intelligence={}
        )
        elapsed = (datetime.now() - t0).total_seconds() * 1000
        
        return {
            "graph_insights":   result,
            "sql_findings":     {"skipped": True, "reason": "graph_only scope"},
            "routing_decision": f"graph_only:{query_type}",
            "current_agent":    "risk_synthesizer",
            "completed_steps":  state["completed_steps"] + ["knowledge_graph"],
            "messages": [AIMessage(content=f"[GraphOnly] patterns={result.get('fraud_patterns',[])}")]
        }
    except Exception as e:
        logger.error(f"[Node:GraphOnly] Error: {e}")
        
        return {
            "graph_insights":  {},
            "sql_findings":    {"skipped": True},
            "routing_decision": "graph_only",
            "current_agent":   "risk_synthesizer",
            "errors":          state["errors"] + [f"GraphOnly: {e}"],
            "completed_steps": state["completed_steps"] + ["knowledge_graph_failed"]
        }


async def sql_only_node(state: FraudAnalyticsState) -> Dict:
    """Run SQL agent only — for run_sql_fraud_check MCP tool."""
    scope      = state.get("investigation_scope", "")
    check_type = scope.split(":")[1] if ":" in scope else "all"
    logger.info(f"[Node:SQLOnly] Running SQL check: {check_type} for {state['entity_id']}")
    
    t0 = datetime.now()
    try:
        result = await sql_agent.run(
            entity_id=state["entity_id"],
            entity_type=state["entity_type"],
            web_context={}
        )
        elapsed = (datetime.now() - t0).total_seconds() * 1000
       
        return {
            "sql_findings":    result,
            "graph_insights":  {"skipped": True, "reason": "sql_only scope"},
            "routing_decision": f"sql_only:{check_type}",
            "current_agent":   "risk_synthesizer",
            "completed_steps": state["completed_steps"] + ["sql_analysis"],
            "messages": [AIMessage(content=f"[SQLOnly] flags={result.get('anomaly_flags',[])}")]
        }
    except Exception as e:
        logger.error(f"[Node:SQLOnly] Error: {e}")
        return {
            "sql_findings":    {},
            "graph_insights":  {"skipped": True},
            "routing_decision": "sql_only",
            "current_agent":   "risk_synthesizer",
            "errors":          state["errors"] + [f"SQLOnly: {e}"],
            "completed_steps": state["completed_steps"] + ["sql_analysis_failed"]
        }


async def quick_exit_node(state: FraudAnalyticsState) -> Dict:
    """Skip heavy SQL/KG analysis for clearly low-risk entities."""
    logger.info(f"[Node:QuickExit] Low risk entity — skipping SQL + KG")
    web_risk = state.get("web_intelligence", {}).get("web_risk_score", 0.0)

    return {
        "sql_findings":    {"skipped": True, "reason": "low_web_risk"},
        "graph_insights":  {"skipped": True, "reason": "low_web_risk"},
        "routing_decision": "quick_exit",
        "current_agent":   "risk_synthesizer",
        "completed_steps": state["completed_steps"] + ["quick_exit"],
        "messages": [AIMessage(content=
            f"[QuickExit] Skipped SQL+KG — web_risk={web_risk:.2f} is low"
        )]
    }


# ─────────────────────────────────────────────────────────────
# NODE 2B — Parallel Analysis (SQL + KG simultaneously)
# ─────────────────────────────────────────────────────────────
async def parallel_analysis_node(state: FraudAnalyticsState) -> Dict:
    """
    Run SQL and Knowledge Graph agents in PARALLEL using asyncio.gather().
    This is the key LangGraph advantage — both run at the same time.
    """
    logger.info(f"[Node:ParallelAnalysis] Running SQL + KG in parallel for {state['entity_id']}")


    t0 = datetime.now()

    # Fire both agents simultaneously
    sql_task = sql_agent.run(
        entity_id=state["entity_id"],
        entity_type=state["entity_type"],
        web_context=state.get("web_intelligence", {})
    )
    kg_task = kg_agent.run(
        entity_id=state["entity_id"],
        entity_type=state["entity_type"],
        sql_findings={},
        web_intelligence=state.get("web_intelligence", {})
    )

    sql_result, kg_result = await asyncio.gather(sql_task, kg_task, return_exceptions=True)
    elapsed = (datetime.now() - t0).total_seconds() * 1000

    # Handle SQL result
    if isinstance(sql_result, Exception):
        logger.error(f"[Node:ParallelAnalysis] SQL failed: {sql_result}")
        sql_result = {}
        sql_msg    = "SQL analysis failed"
    else:
        sql_msg = f"SQL: {sql_result.get('transaction_count', 0)} txns | flags={sql_result.get('anomaly_flags', [])}"

    # Handle KG result
    if isinstance(kg_result, Exception):
        logger.error(f"[Node:ParallelAnalysis] KG failed: {kg_result}")
        kg_result = {}
        kg_msg    = "KG analysis failed"
    else:
        kg_msg = f"KG: clusters={kg_result.get('suspicious_clusters', 0)} | patterns={kg_result.get('fraud_patterns', [])}"

    logger.info(f"[Node:ParallelAnalysis] {sql_msg}")
    logger.info(f"[Node:ParallelAnalysis] {kg_msg}")

    return {
        "sql_findings":     sql_result,
        "graph_insights":   kg_result,
        "routing_decision": "full_analysis",
        "current_agent":    "risk_synthesizer",
        "completed_steps":  state["completed_steps"] + ["sql_analysis", "knowledge_graph"],
        "messages": [
            AIMessage(content=f"[SQLAnalyst] {sql_msg}"),
            AIMessage(content=f"[KGAgent]    {kg_msg}"),
        ]
    }


# ─────────────────────────────────────────────────────────────
# NODE 3 — Risk Synthesis (always runs last)
# ─────────────────────────────────────────────────────────────
async def risk_synthesizer_node(state: FraudAnalyticsState) -> Dict:
    logger.info(f"[Node:RiskSynthesizer] Generating report for {state['entity_id']}")

    t0 = datetime.now()
    try:
       
        result = await synth_agent.run(
    entity_id=state["entity_id"],
    entity_type=state["entity_type"],
    web_intelligence=state.get("web_intelligence", {}),
    sql_findings=state.get("sql_findings", {}),
    graph_insights=state.get("graph_insights", {}),
    customer_info=state.get("customer_info", {}),   # ← YEH ADD KARO
)
        elapsed = (datetime.now() - t0).total_seconds() * 1000
        logger.info(f"[Node:RiskSynthesizer] risk={result['risk_level']} ({result['risk_score']:.2f})")
        return {
            "risk_score":      result["risk_score"],
            "risk_level":      result["risk_level"],
            "fraud_indicators": result.get("fraud_indicators", []),
            "recommendations": result.get("recommendations", []),
            "report":          result.get("report", ""),
            "file_sar":        result.get("file_sar", False),
            "current_agent":   "done",
            "completed_steps": state["completed_steps"] + ["risk_synthesis"],
            "messages": [AIMessage(content=
                f"[RiskSynthesizer] FINAL: {result['risk_level']} | "
                f"score={result['risk_score']:.2f} | SAR={result.get('file_sar', False)}"
            )]
        }
    except Exception as e:
        logger.error(f"[Node:RiskSynthesizer] Error: {e}")
        return {
            "risk_score":    0.0,
            "risk_level":    "UNKNOWN",
            "current_agent": "done",
            "errors":        state["errors"] + [f"RiskSynthesizer: {e}"],
            "completed_steps": state["completed_steps"] + ["risk_synthesis_failed"]
        }


# ─────────────────────────────────────────────────────────────
# Build the LangGraph
# ─────────────────────────────────────────────────────────────
def build_fraud_graph() -> StateGraph:
    graph = StateGraph(FraudAnalyticsState)

    # ── Add nodes ─────────────────────────────────────────────
    graph.add_node("web_scraping",      web_scraping_node)
    graph.add_node("quick_exit",        quick_exit_node)
    graph.add_node("sql_only",          sql_only_node)
    graph.add_node("graph_only",        graph_only_node)
    graph.add_node("parallel_analysis", parallel_analysis_node)
    graph.add_node("risk_synthesis",    risk_synthesizer_node)

    # ── Entry point ───────────────────────────────────────────
    graph.set_entry_point("web_scraping")

    # ── Conditional routing after web scraping ────────────────
    graph.add_conditional_edges(
        "web_scraping",
        route_after_web,
        {
            "quick_exit":        "quick_exit",
            "sql_only":          "sql_only",
            "graph_only":        "graph_only",
            "parallel_analysis": "parallel_analysis",
        }
    )

    # ── All paths converge to risk_synthesis ──────────────────
    graph.add_edge("quick_exit",        "risk_synthesis")
    graph.add_edge("sql_only",          "risk_synthesis")
    graph.add_edge("graph_only",        "risk_synthesis")
    graph.add_edge("parallel_analysis", "risk_synthesis")
    graph.add_edge("risk_synthesis",    END)

    return graph.compile()


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
async def investigate_entity(
    entity_id:            str,
    entity_type:          str = "customer",
    investigation_scope:  str = "full",
    user_role:            str = "admin",
    user_id:              str = "USR_001",
    db_schema:            str = "admin",
    customer_info:        dict = None,
) -> FraudAnalyticsState:

    graph    = build_fraud_graph()

    initial_state: FraudAnalyticsState = {
        "messages":            [HumanMessage(content=f"Investigate {entity_type}: {entity_id}")],
        "entity_id":           entity_id,
        "entity_type":         entity_type,
        "investigation_scope": investigation_scope,
        "web_intelligence":    {},
        "sql_findings":        {},
        "graph_insights":      {},
        "risk_score":          None,
        "risk_level":          None,
        "fraud_indicators":    [],
        "recommendations":     [],
        "report":              None,
        "file_sar":            False,
        # New agent findings
        "linkedin_findings":   None,
        "youtube_findings":    None,
        "itr_credit_findings": None,
        "income_findings":     None,
        "customer_info":       customer_info or {},
        # User context
        "user_role":           user_role,
        "user_id":             user_id,
        "db_schema":           db_schema,
        "current_agent":       "web_scraper",
        "routing_decision":    "",
        "errors":              [],
        "completed_steps":     []
    }

    logger.info(f"{'='*50}")
    logger.info(f"Starting LangGraph investigation: {entity_type}:{entity_id}")
    logger.info(f"{'='*50}")

    final_state = await graph.ainvoke(initial_state)

    # ── Run role-based exclusive agents after main graph ──
    allowed_agents = ROLE_AGENTS.get(user_role, ROLE_AGENTS["admin"])

    if "linkedin" in allowed_agents:
        try:
            final_state = await run_linkedin_agent(final_state)
            final_state["completed_steps"].append("linkedin")
        except Exception as e:
            final_state["errors"].append(f"linkedin_agent: {e}")

    if "itr_credit" in allowed_agents:
        try:
            final_state = await run_itr_credit_agent(final_state)
            final_state["completed_steps"].append("itr_credit")
        except Exception as e:
            final_state["errors"].append(f"itr_credit_agent: {e}")

    if "youtube" in allowed_agents:
        try:
            final_state = await run_youtube_agent(final_state)
            final_state["completed_steps"].append("youtube")
        except Exception as e:
            final_state["errors"].append(f"youtube_agent: {e}")

    if "income" in allowed_agents:
        try:
            final_state = await run_income_agent(final_state)
            final_state["completed_steps"].append("income")
        except Exception as e:
            final_state["errors"].append(f"income_agent: {e}")


    logger.info(f"Investigation complete | risk={final_state.get('risk_level')} | "
                f"steps={final_state.get('completed_steps')} | "
                f"errors={final_state.get('errors')}")

    return final_state


if __name__ == "__main__":
    import sys
    eid = sys.argv[1] if len(sys.argv) > 1 else "CUST_0042"
    result = asyncio.run(investigate_entity(eid, "customer", "full"))
    print(json.dumps({
        "entity_id":        result["entity_id"],
        "risk_score":       result["risk_score"],
        "risk_level":       result["risk_level"],
        "routing_decision": result["routing_decision"],
        "fraud_indicators": result["fraud_indicators"],
        "completed_steps":  result["completed_steps"],
        "errors":           result["errors"]
    }, indent=2, default=str))