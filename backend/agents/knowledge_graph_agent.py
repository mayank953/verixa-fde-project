"""
Knowledge Graph Agent (Neo4j)
==============================
Builds and traverses a knowledge graph to detect:
- Ring fraud networks (connected entity clusters)
- Shared identity attributes across suspicious accounts
- Money mule chain detection
- Hidden beneficial ownership patterns

Graph Schema:
  Nodes: Customer, Merchant, Account, Device, IP, Address, PhoneNumber, Email
  Edges: TRANSACTED_WITH, SHARES_DEVICE, SHARES_IP, LINKED_TO, OWNS_ACCOUNT
"""

from __future__ import annotations
import os
from neo4j import GraphDatabase
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime
from backend.models.model import GraphNetworkSynthesis

from backend.logger import GLOBAL_LOGGER as logger

# ─────────────────────────────────────────────
# Cypher Query Templates
# ─────────────────────────────────────────────
CYPHER_QUERIES = {
    "entity_neighborhood": """
        MATCH (e {entity_id: $entity_id})-[r*1..2]-(neighbor)
        RETURN e, r, neighbor
        LIMIT 50
    """,

    "shared_device_network": """
        MATCH (c1:Customer {entity_id: $entity_id})-[:USES_DEVICE]->(d:Device)<-[:USES_DEVICE]-(c2:Customer)
        WHERE c1 <> c2
        RETURN c2.entity_id AS linked_customer, d.device_id AS shared_device,
               c2.risk_score AS their_risk_score
        ORDER BY their_risk_score DESC
    """,

    "money_mule_chain": """
        MATCH path = (source {entity_id: $entity_id})-[:TRANSFERRED_TO*1..5]->(dest)
        WHERE dest.is_flagged = true
        RETURN path, length(path) as chain_length
        ORDER BY chain_length ASC
        LIMIT 10
    """,

    "ring_fraud_detection": """
        MATCH (c:Customer {entity_id: $entity_id})-[:TRANSACTED_WITH]->(m:Merchant)<-[:TRANSACTED_WITH]-(c2:Customer)
        WHERE c <> c2 AND c2.risk_level IN ['HIGH', 'CRITICAL']
        WITH m, COUNT(DISTINCT c2) AS co_transactors
        WHERE co_transactors >= 2
        RETURN m.entity_id AS merchant_id, m.name AS merchant_name, co_transactors
        ORDER BY co_transactors DESC
    """,

    "shared_identity_attributes": """
        MATCH (c:Customer {entity_id: $entity_id})-[:LINKED_TO]->(other:Customer)
        WHERE c <> other
        RETURN other.entity_id AS linked_entity,
               other.risk_level AS their_risk_level,
               other.risk_score AS their_risk_score,
               other.city AS their_city
        ORDER BY other.risk_score DESC
    """,

    "beneficial_ownership": """
        MATCH (c:Customer {entity_id: $entity_id})-[:CONTROLS]->(a:Account)
        RETURN a.account_id AS owned_account, a.bank AS bank,
               a.is_flagged AS flagged, a.balance AS balance
        LIMIT 20
    """
}



class KnowledgeGraphAgent:
    """Agent that builds and queries Neo4j knowledge graph for fraud network detection."""

    SYSTEM_PROMPT = """You are a fraud network analyst specialising in graph-based detection.
Given Neo4j graph query results showing entity relationships, identify:
- Suspicious network clusters indicating organised fraud
- Shared identity attributes suggesting synthetic/stolen identities
- Money mule chains and layering patterns
- Ring fraud networks

Be precise. Only report relationships and patterns clearly present in the query results."""

    def __init__(self, llm, neo4j_uri: Optional[str] = None, neo4j_user: str = "neo4j", neo4j_password: str = "password"):
        self.llm = llm                         
        self.neo4j_uri      = neo4j_uri or os.getenv("NEO4J_URI")
        self.neo4j_user     = neo4j_user or os.getenv("NEO4J_USER", "neo4j")
        self.neo4j_password = neo4j_password or os.getenv("NEO4J_PASSWORD", "password")
        self._driver = None

    def _get_driver(self):
        if self._driver is None:
            try:
                
                driver = GraphDatabase.driver(
                    self.neo4j_uri,
                    auth=(self.neo4j_user, self.neo4j_password),
                    connection_timeout=3,        # fail fast, don't hang
                    max_connection_lifetime=60,
                )
                # verify_connectivity() actually opens a connection — this is
                # where the real error surfaces, so we catch it here cleanly
                driver.verify_connectivity()
                self._driver = driver
                logger.info("Neo4j connection established")
            except Exception as e:
                logger.error(f"Neo4j connection failed: {type(e).__name__}: {e}")
                raise ConnectionError(
                    f"Neo4j unavailable at {self.neo4j_uri}. "
                    f"Ensure NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD are set correctly. "
                    f"Reason: {type(e).__name__}: {e}"
                )
        return self._driver

    def _run_cypher(self, query_name: str, entity_id: str) -> List[Dict]:
        query = CYPHER_QUERIES.get(query_name, "")
        driver = self._get_driver()
        try:
            with driver.session() as session:
                result = session.run(query, entity_id=entity_id)
                return [dict(record) for record in result]
        except Exception as e:
            logger.error(f"Cypher query '{query_name}' failed: {e}")
            raise RuntimeError(f"Neo4j query '{query_name}' failed: {type(e).__name__}: {e}")

    @staticmethod
    def upsert_entity_to_graph(driver, entity_id: str, entity_type: str, properties: Dict) -> None:
        """Upsert an entity node into Neo4j from SQL/web findings."""
        cypher = f"""
        MERGE (e:{entity_type} {{entity_id: $entity_id}})
        SET e += $properties
        """
        with driver.session() as session:
            session.run(cypher, entity_id=entity_id, properties=properties)

    @staticmethod
    def create_relationship(driver, from_id: str, to_id: str, rel_type: str, properties: Dict = None) -> None:
        """Create a relationship between two entities in the graph."""
        cypher = f"""
        MATCH (a {{entity_id: $from_id}})
        MATCH (b {{entity_id: $to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $properties
        """
        with driver.session() as session:
            session.run(cypher, from_id=from_id, to_id=to_id, properties=properties or {})

    async def run(
        self, entity_id: str, entity_type: str,
        sql_findings: Dict, web_intelligence: Dict
    ) -> Dict[str, Any]:
        
        logger.info(f"KnowledgeGraphAgent running for {entity_type}:{entity_id}")
        driver = self._get_driver()

        # ── 1. Upsert entity with enriched data ──────────────────────────────
        enriched_props = {
            "entity_type": entity_type,
            "last_analyzed": datetime.utcnow().isoformat() + "Z",
            "sql_risk_score": sql_findings.get("sql_risk_score", 0),
            "web_risk_score": web_intelligence.get("web_risk_score", 0),
            "anomaly_flags": str(sql_findings.get("anomaly_flags", []))
        }
        try:
            self.upsert_entity_to_graph(driver, entity_id, entity_type.capitalize(), enriched_props)
        except Exception as e:
            logger.warning(f"Graph upsert failed: {e}")

        # ── 2. Run all detection queries ──────────────────────────────────────
        graph_results = {}
        for query_name in CYPHER_QUERIES:
            graph_results[query_name] = self._run_cypher(query_name, entity_id)

        # ── 3. Derive risk signals ────────────────────────────────────────────
        suspicious_clusters = 0
        fraud_patterns      = []
        connected_entities  = []

        shared_devices = graph_results.get("shared_device_network", [])
        if len(shared_devices) >= 2:
            fraud_patterns.append("SHARED_DEVICE_CLUSTER")
            suspicious_clusters += 1
            connected_entities += [r.get("linked_customer") for r in shared_devices]

        mule_chains = graph_results.get("money_mule_chain", [])
        if mule_chains:
            fraud_patterns.append("MONEY_MULE_CHAIN")
            suspicious_clusters += 1

        ring_fraud = graph_results.get("ring_fraud_detection", [])
        if ring_fraud:
            fraud_patterns.append("RING_FRAUD_NETWORK")
            suspicious_clusters += len(ring_fraud)

        shared_ids = graph_results.get("shared_identity_attributes", [])
        if any(r.get("sharing_count", 0) > 5 for r in shared_ids):
            fraud_patterns.append("SYNTHETIC_IDENTITY_SIGNAL")
            suspicious_clusters += 1

        # ── 4. LLM structured synthesis ───────────────────────
        synthesis_prompt = f"""
Entity: {entity_id} ({entity_type})
Graph Query Results: {graph_results}
Detected Fraud Patterns: {fraud_patterns}
Suspicious Clusters: {suspicious_clusters}

Synthesise the graph findings into a structured network risk assessment.
Only report relationships and patterns clearly present in the results above.
"""
        graph_synthesis: Optional[GraphNetworkSynthesis] = None
        try:
            structured_llm = self.llm.with_structured_output(GraphNetworkSynthesis)
            graph_synthesis = await structured_llm.ainvoke([
                SystemMessage(content=self.SYSTEM_PROMPT),
                HumanMessage(content=synthesis_prompt)
            ])
        except Exception as e:
            logger.warning(f"[KnowledgeGraphAgent] Structured LLM failed: {e}")
            risk_map = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}
            graph_synthesis = GraphNetworkSynthesis(
                network_risk_level=risk_map.get(min(suspicious_clusters, 2), "CRITICAL"),
                key_relationships=[f"Cluster detected: {p}" for p in fraud_patterns],
                recommended_investigation_paths=[
                    f"Investigate connected entities: {connected_entities[:3]}"
                ] if connected_entities else ["No high-risk paths identified"]
            )


        return {
            "entity_id":                       entity_id,
            "graph_results":                   graph_results,
            "suspicious_clusters":             suspicious_clusters,
            "fraud_patterns":                  fraud_patterns,
            "connected_entities":              list(set(connected_entities)),
            "graph_risk_score":                min(suspicious_clusters * 0.2, 0.9),
            # Structured LLM output fields
            "network_risk_level":              graph_synthesis.network_risk_level,
            "key_relationships":               graph_synthesis.key_relationships,
            "recommended_investigation_paths": graph_synthesis.recommended_investigation_paths,
            "timestamp":                       datetime.utcnow().isoformat() + "Z"
        }