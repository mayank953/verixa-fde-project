"""Baseline checks for this repository. No model key and no internet needed.
Tests that need the databases are skipped when `make up` has not been run."""
import hashlib, json, os, re, subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"
FIX = ROOT / "data" / "fixtures"
sys.path.insert(0, str(ROOT))


def _all_generated():
    return b"".join(p.read_bytes() for p in sorted(list(SEED.glob("*")) + list(FIX.glob("*.json"))))


def test_generator_is_repeatable():
    before = hashlib.sha256(_all_generated()).hexdigest()
    subprocess.run([sys.executable, str(ROOT / "data" / "generate.py")], check=True, capture_output=True)
    assert hashlib.sha256(_all_generated()).hexdigest() == before


def test_manifest_counts():
    m = json.loads((SEED / "MANIFEST.json").read_text())
    assert (m["customers"], m["merchants"]) == (54, 20)
    assert m["transactions"] > 3000 and m["chargebacks"] > 50


def test_everything_is_fictional():
    text = _all_generated().decode("utf-8", "ignore")
    emails = set(re.findall(r"[\w.+-]+@[\w.-]+", text))
    assert emails and all(e.endswith(".example") for e in emails), "every e-mail must use the reserved .example domain"
    names = set(re.findall(r"INSERT INTO customers.*?;", text, flags=re.S))
    assert names, "customers must be generated, never typed from a real list"


def test_no_secrets_in_example_env():
    env = (ROOT / ".env.example").read_text()
    assert "AIza" not in env and "apify_api_" not in env


def _db():
    try:
        import psycopg2
        return psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://fraud_user:password@localhost:5432/fraud_db"), connect_timeout=2)
    except Exception:
        pytest.skip("PostgreSQL is not running (run: make up)")


def test_database_matches_manifest():
    m = json.loads((SEED / "MANIFEST.json").read_text())
    cur = _db().cursor()
    for table, key in (("customers", "customers"), ("transactions", "transactions"), ("chargebacks", "chargebacks")):
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        assert cur.fetchone()[0] == m[key], table


STRUCT = """SELECT COUNT(*) FROM (SELECT 1 FROM transactions t JOIN customers c ON c.customer_id=t.customer_id
 JOIN jurisdiction_rules r ON r.country_code=c.home_country WHERE t.customer_id=%s
 GROUP BY DATE(t.txn_timestamp), r.structuring_low, r.structuring_high, r.structuring_min_count
 HAVING COUNT(*) FILTER (WHERE t.amount BETWEEN r.structuring_low AND r.structuring_high) >= r.structuring_min_count) x"""


def test_structuring_is_detected_only_for_the_designed_customer():
    cur = _db().cursor()
    cur.execute(STRUCT, ("CUST_0042",)); assert cur.fetchone()[0] >= 3
    cur.execute(STRUCT, ("CUST_0001",)); assert cur.fetchone()[0] == 0


def test_chargeback_and_round_amount_patterns():
    cur = _db().cursor()
    cur.execute("SELECT COUNT(*)*100.0/(SELECT COUNT(*) FROM transactions WHERE customer_id='CUST_0017') FROM chargebacks WHERE customer_id='CUST_0017'")
    assert float(cur.fetchone()[0]) >= 20
    cur.execute("SELECT COUNT(*) FILTER (WHERE amount%1000=0)*100.0/COUNT(*) FROM transactions WHERE customer_id='CUST_0019'")
    assert float(cur.fetchone()[0]) >= 50


def test_graph_has_the_ring_and_the_mule_chain():
    try:
        from neo4j import GraphDatabase
        d = GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"), auth=("neo4j", os.getenv("NEO4J_PASSWORD", "password")))
        d.verify_connectivity()
    except Exception:
        pytest.skip("Neo4j is not running (run: make up)")
    with d.session() as s:
        ring = s.run("MATCH (a:Customer {entity_id:'CUST_0042'})-[:USES_DEVICE]->(:Device)<-[:USES_DEVICE]-(b:Customer) RETURN count(DISTINCT b) AS n").single()["n"]
        chain = s.run("MATCH p=(:Customer {entity_id:'CUST_0030'})-[:TRANSFERRED_TO*1..5]->(d) WHERE d.is_flagged=true RETURN max(length(p)) AS n").single()["n"]
        clean = s.run("MATCH (c:Customer {entity_id:'CUST_0001'})-[:TRANSACTED_WITH]->() RETURN count(*) AS n").single()["n"]
    assert ring >= 5 and chain == 2 and clean == 0


def test_sample_mode_answers_without_the_internet(monkeypatch):
    import asyncio
    monkeypatch.setenv("EXTERNAL_MODE", "sample")
    from backend import providers
    async def go():
        async with providers.async_client() as c:
            r = await c.get("https://www.disify.com/api/email/someone@tempmail.example")
            return r.json()
    assert asyncio.run(go())["disposable"] is True


def test_live_is_the_code_default(monkeypatch):
    monkeypatch.delenv("EXTERNAL_MODE", raising=False)
    from backend import providers
    assert providers.external_mode() == "live"
