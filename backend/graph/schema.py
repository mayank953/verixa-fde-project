"""
Knowledge Graph Schema & Initialization
========================================
Sets up Neo4j schema for fraud analytics:
- Node labels and constraints
- Relationship types
- Index creation for query performance
- Sample data seeding for development
"""

from __future__ import annotations
import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import random

from logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Schema Definition
# ─────────────────────────────────────────────
SCHEMA_CYPHER = [
    # ── Constraints (uniqueness) ──────────────────────────────────────────
    "CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.entity_id IS UNIQUE",
    "CREATE CONSTRAINT merchant_id IF NOT EXISTS FOR (m:Merchant) REQUIRE m.entity_id IS UNIQUE",
    "CREATE CONSTRAINT account_id  IF NOT EXISTS FOR (a:Account)  REQUIRE a.account_id IS UNIQUE",
    "CREATE CONSTRAINT device_id   IF NOT EXISTS FOR (d:Device)   REQUIRE d.device_id IS UNIQUE",
    "CREATE CONSTRAINT ip_addr     IF NOT EXISTS FOR (i:IPAddress) REQUIRE i.ip_address IS UNIQUE",
    "CREATE CONSTRAINT email_id    IF NOT EXISTS FOR (e:Email)    REQUIRE e.address IS UNIQUE",
    "CREATE CONSTRAINT phone_id    IF NOT EXISTS FOR (p:Phone)    REQUIRE p.number IS UNIQUE",

    # ── Indexes ───────────────────────────────────────────────────────────
    "CREATE INDEX customer_risk   IF NOT EXISTS FOR (c:Customer)  ON (c.risk_level)",
    "CREATE INDEX merchant_risk   IF NOT EXISTS FOR (m:Merchant)  ON (m.risk_category)",
    "CREATE INDEX txn_timestamp   IF NOT EXISTS FOR ()-[t:TRANSACTED_WITH]-() ON (t.timestamp)",
    "CREATE INDEX entity_flagged  IF NOT EXISTS FOR (n:Customer)  ON (n.is_flagged)",

    # ── Full-text search ──────────────────────────────────────────────────
    """CREATE FULLTEXT INDEX entity_search IF NOT EXISTS
       FOR (n:Customer|Merchant) ON EACH [n.entity_id, n.name, n.email]"""
]


# ─────────────────────────────────────────────
# Relationship Types Reference
# ─────────────────────────────────────────────
RELATIONSHIP_TYPES = {
    "TRANSACTED_WITH":   "Customer -[amount, timestamp, channel]-> Merchant",
    "USES_DEVICE":       "Customer -[first_seen, last_seen]-> Device",
    "USES_IP":           "Customer -[session_count]-> IPAddress",
    "HAS_ACCOUNT":       "Customer -[opened_date]-> Account",
    "HAS_EMAIL":         "Customer -> Email",
    "HAS_PHONE":         "Customer -> Phone",
    "TRANSFERRED_TO":    "Account  -[amount, timestamp]-> Account",
    "SHARES_DEVICE":     "Customer -[overlap_count]-> Customer  (derived)",
    "CO_TRANSACTS_WITH": "Customer -[merchant_id, count]-> Customer (derived)",
    "LINKED_TO":         "Customer -[link_type, confidence]-> Customer",
    "OWNS":              "Customer -[ownership_pct]-> Merchant",
    "CONTROLS":          "Customer -> Account",
    "IS_FLAGGED_BY":     "Customer|Merchant -[flag_type, date]-> :FraudAlert"
}


# ─────────────────────────────────────────────
# Schema Initializer
# ─────────────────────────────────────────────
class GraphSchemaManager:
    
    def __init__(self, uri: str = None, user: str = None, password: str = None):
        self.uri      = uri or os.getenv("NEO4J_URI")
        self.user     = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self._driver  = None

    def _get_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        return self._driver

    def apply_schema(self) -> Dict[str, Any]:
        driver = self._get_driver()
        results = {"applied": [], "errors": []}
        
        with driver.session() as session:
            for cypher in SCHEMA_CYPHER:
                try:
                    session.run(cypher)
                    results["applied"].append(cypher[:60] + "...")
                    logger.info(f"Applied: {cypher[:60]}")
                except Exception as e:
                    results["errors"].append(str(e))
                    logger.warning(f"Schema stmt failed (may already exist): {e}")
        
        return results

    def seed_sample_data(self, n_customers: int = 20, n_merchants: int = 10) -> Dict[str, int]:
        """Seed development data including a known fraud ring."""
        driver = self._get_driver()
        
        with driver.session() as session:
            # Create merchants
            for i in range(n_merchants):
                risk_cat = "HIGH" if i < 2 else "MEDIUM" if i < 5 else "LOW"
                session.run("""
                    MERGE (m:Merchant {entity_id: $id})
                    SET m.merchant_name = $name, m.risk_category = $risk
                """, id=f"MERCH_{i:04d}", name=f"Merchant_{i}", risk=risk_cat)

            # Create customers (last 5 form a fraud ring)
            for i in range(n_customers):
                is_ring     = i >= n_customers - 5
                risk_level  = "HIGH" if is_ring else "LOW"
                is_flagged  = is_ring
                session.run("""
                    MERGE (c:Customer {entity_id: $id})
                    SET c.name = $name, c.risk_level = $risk, c.is_flagged = $flagged,
                        c.risk_score = $score, c.home_country = 'IN'
                """, id=f"CUST_{i:04d}", name=f"Customer_{i}",
                     risk=risk_level, flagged=is_flagged,
                     score=0.8 if is_ring else 0.1)

            # Connect fraud ring to shared device
            session.run("""
                MERGE (d:Device {device_id: 'DEV_RING_001'})
                SET d.device_type = 'mobile', d.os = 'Android'
            """)
            for i in range(n_customers - 5, n_customers):
                session.run("""
                    MATCH (c:Customer {entity_id: $cust_id})
                    MATCH (d:Device {device_id: 'DEV_RING_001'})
                    MERGE (c)-[:USES_DEVICE {first_seen: date()}]->(d)
                """, cust_id=f"CUST_{i:04d}")

            # Connect ring customers to high-risk merchant
            for i in range(n_customers - 5, n_customers):
                session.run("""
                    MATCH (c:Customer {entity_id: $cust_id})
                    MATCH (m:Merchant {entity_id: 'MERCH_0000'})
                    MERGE (c)-[:TRANSACTED_WITH {
                        amount: $amount, channel: 'online',
                        timestamp: datetime()
                    }]->(m)
                """, cust_id=f"CUST_{i:04d}", amount=random.randint(8000, 9999))

        return {
            "customers_seeded": n_customers,
            "merchants_seeded": n_merchants,
            "fraud_ring_size":  5
        }

    def get_graph_stats(self) -> Dict[str, Any]:
        driver = self._get_driver()
        with driver.session() as session:
            stats = {}
            for label in ["Customer", "Merchant", "Device", "Account", "IPAddress"]:
                result = session.run(f"MATCH (n:{label}) RETURN COUNT(n) as count")
                stats[f"{label.lower()}_count"] = result.single()["count"]
            
            rel_result = session.run("MATCH ()-[r]->() RETURN COUNT(r) as count")
            stats["relationship_count"] = rel_result.single()["count"]
            
            flagged_result = session.run("MATCH (n:Customer {is_flagged: true}) RETURN COUNT(n) as count")
            stats["flagged_customers"] = flagged_result.single()["count"]
        
        return stats

    def export_subgraph(self, entity_id: str, depth: int = 2) -> Dict[str, Any]:
        """Export entity subgraph as JSON for visualisation."""
        driver = self._get_driver()
        with driver.session() as session:
            result = session.run("""
                MATCH path = (e {entity_id: $entity_id})-[*1..$depth]-(neighbor)
                RETURN 
                    [n in nodes(path) | {id: n.entity_id, labels: labels(n), risk: n.risk_level}] as nodes,
                    [r in relationships(path) | {type: type(r), from: startNode(r).entity_id, to: endNode(r).entity_id}] as edges
                LIMIT 100
            """, entity_id=entity_id, depth=depth)
            
            all_nodes = {}
            all_edges = []
            for record in result:
                for node in record["nodes"]:
                    all_nodes[node["id"]] = node
                all_edges.extend(record["edges"])
            
            return {
                "entity_id": entity_id,
                "depth": depth,
                "nodes": list(all_nodes.values()),
                "edges": all_edges,
                "node_count": len(all_nodes),
                "edge_count": len(all_edges)
            }

    def close(self):
        if self._driver:
            self._driver.close()


# ─────────────────────────────────────────────
# CLI Entry
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage fraud analytics knowledge graph")
    parser.add_argument("command", choices=["schema", "seed", "stats", "export"])
    parser.add_argument("--entity-id", default="CUST_0042")
    parser.add_argument("--depth",     type=int, default=2)
    args = parser.parse_args()

    manager = GraphSchemaManager()
    
    if args.command == "schema":
        result = manager.apply_schema()
        print(json.dumps(result, indent=2))
    elif args.command == "seed":
        result = manager.seed_sample_data()
        print(json.dumps(result, indent=2))
    elif args.command == "stats":
        result = manager.get_graph_stats()
        print(json.dumps(result, indent=2))
    elif args.command == "export":
        result = manager.export_subgraph(args.entity_id, args.depth)
        print(json.dumps(result, indent=2))
    
    manager.close()