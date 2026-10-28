"""
models.py — Pydantic Models for All Agent Outputs
===================================================
Every LLM structured output is defined here.
Agents use llm.with_structured_output(Model) — no manual JSON parsing anywhere.
"""

from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field



# ══════════════════════════════════════════════════════════════
# SQL ANALYST AGENT
# ══════════════════════════════════════════════════════════════

class SQLAnalysisSynthesis(BaseModel):
    """LLM synthesis of SQL fraud query results."""
    summary: str = Field(
        description="Concise summary of transaction pattern findings"
    )
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        description="Overall severity of SQL findings"
    )
    key_findings: List[str] = Field(
        description="List of specific anomalies found in transaction data"
    )
    recommended_escalation: bool = Field(
        description="True if findings warrant immediate human review"
    )


# ═════════════════════════════════════════════

# ═════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════
# WEB SCRAPER AGENT
# ══════════════════════════════════════════════════════════════

class WebRiskSignal(BaseModel):
    """A single web-sourced risk signal."""
    type: Literal[
        "adverse_news", "sanctions_hit", "data_breach_exposure", "disposable_email"
    ] = Field(description="Type of web risk signal detected")
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        description="Severity level of this signal"
    )
    detail: str = Field(description="Human-readable explanation of the signal")




# ══════════════════════════════════════════════════════════════
# NEWS INTELLIGENCE (used inside WebIntelligenceSynthesis)
# ══════════════════════════════════════════════════════════════

class NewsArticleSummary(BaseModel):
    """LLM-generated summary of a single news article."""
    headline: str = Field(description="Article headline")
    date: str = Field(description="Publication date YYYY-MM-DD")
    source: str = Field(description="News source name")
    fraud_detail: str = Field(
        description="Specific fraud/crime detail from this article — what happened, who, when, amounts if mentioned"
    )
    risk_keywords: List[str] = Field(description="Risk keywords found in article")

class WebIntelligenceSynthesis(BaseModel):
    """LLM synthesis of all web API results for an entity."""
    summary: str = Field(
        description="2-3 sentence summary of web intelligence findings"
    )
    news_narrative: str = Field(
        description=(
            "Detailed paragraph about news findings — specific fraud/crime alleged, "
            "institutions involved, amounts, dates, legal status. "
            "Write 'No adverse news found.' if nothing negative."
        )
    )
    key_risks: List[str] = Field(
        description="List of specific risk factors found, empty list if none"
    )
    confidence_score: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in these findings (0.0=uncertain, 1.0=very confident)"
    )
    recommended_action: str = Field(
        description="Specific next step recommended based on web findings"
    )



# ══════════════════════════════════════════════════════════════
# KNOWLEDGE GRAPH AGENT
# ══════════════════════════════════════════════════════════════

class GraphNetworkSynthesis(BaseModel):
    """LLM synthesis of Neo4j graph traversal results."""
    network_risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        description="Risk level based on network connections"
    )
    key_relationships: List[str] = Field(
        description="Most significant entity relationships found in the graph"
    )
    recommended_investigation_paths: List[str] = Field(
        description="Specific graph paths or entities that warrant deeper investigation"
    )


# ══════════════════════════════════════════════════════════════
# RISK SYNTHESIZER AGENT
# ══════════════════════════════════════════════════════════════

class RiskSynthesisOutput(BaseModel):
    """Final risk assessment output from the synthesizer agent."""
    recommendations: List[str] = Field(
        min_length=3, max_length=5,
        description="3-5 specific, actionable recommendations proportional to risk level"
    )
    file_sar: bool = Field(
        description="True if a Suspicious Activity Report should be filed"
    )
    monitoring_days: int = Field(
        ge=7, le=365,
        description="Recommended monitoring period in days"
    )
    rationale: str = Field(
        description="Brief explanation of why this risk level was assigned"
    )