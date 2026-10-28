#!/usr/bin/env python3
"""Verixa Bank: seeded synthetic data generator (standard library only).

Writes data/seed/seed.sql (PostgreSQL), data/seed/neo4j_seed.cypher (Neo4j) and data/seed/MANIFEST.json.
Same seed, same files: the counts never change between runs.

EVERYTHING HERE IS FICTIONAL. Names, e-mail addresses (reserved .example domain), phone numbers, IP addresses
(documentation ranges of RFC 5737) and company names are invented. Any resemblance to a real person or company
is a coincidence. Thresholds in jurisdiction_rules are ILLUSTRATIONS, not legal values.

The graph is built from the same generated transactions as the tables, so the two databases always agree.
"""
import json, os, random, hashlib
from datetime import datetime, timedelta

SEED = 20261027
ASOF = datetime(2026, 10, 1, 12, 0, 0)          # fixed "today" so timestamps are repeatable
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed")
rng = random.Random(SEED)

REPORTING_CCY = "USD"
FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "SGD": 0.74, "AED": 0.272, "CAD": 0.73, "AUD": 0.65}   # illustrative
COUNTRY = {  # country: (currency, calling code, cities)
    "US": ("USD", "1", ["Austin", "Denver", "Boston", "Seattle"]), "GB": ("GBP", "44", ["London", "Leeds", "Bristol"]),
    "DE": ("EUR", "49", ["Hamburg", "Munich", "Berlin"]), "FR": ("EUR", "33", ["Lyon", "Lille", "Nantes"]),
    "SG": ("SGD", "65", ["Singapore"]), "AE": ("AED", "971", ["Dubai", "Abu Dhabi"]),
    "CA": ("CAD", "1", ["Toronto", "Calgary"]), "AU": ("AUD", "61", ["Melbourne", "Perth"])}

# illustrative near-threshold bands (reporting currency) used by the structuring check: country -> (low, high, min count, chargeback limit %)
RULES = {"US": (8000, 9999, 2, 5), "GB": (7000, 8999, 2, 5), "DE": (7500, 9499, 2, 5), "FR": (7500, 9499, 2, 5),
         "SG": (6500, 7999, 2, 5), "AE": (3000, 3999, 2, 5), "CA": (7000, 9999, 2, 5), "AU": (7000, 9499, 2, 5)}

MERCH = [  # id, name, risk, country, category
 ("MERCH_0001", "FastCash Remit", "HIGH", "US", "Money Transfer"), ("MERCH_0002", "QuickSend Global", "HIGH", "AE", "Money Transfer"),
 ("MERCH_0003", "ChainSwap Exchange", "HIGH", "SG", "Cryptocurrency"), ("MERCH_0004", "ShadowFX Trading", "HIGH", "US", "Trading"),
 ("MERCH_0005", "NightOwl Casino", "HIGH", "MT", "Gambling"),
 ("MERCH_0006", "MedPay Pharma", "MEDIUM", "GB", "Pharmacy"), ("MERCH_0007", "GoldLeaf Jewellers", "MEDIUM", "AE", "Jewellery"),
 ("MERCH_0008", "QuickLoan Finance", "MEDIUM", "US", "Finance"), ("MERCH_0009", "TravelXpress", "MEDIUM", "SG", "Travel"),
 ("MERCH_0010", "ElectroBulk", "MEDIUM", "DE", "Electronics"),
 ("MERCH_0011", "GreenLeaf Market", "LOW", "GB", "Grocery"), ("MERCH_0012", "CartHub Marketplace", "LOW", "US", "E-commerce"),
 ("MERCH_0013", "BlueFork Delivery", "LOW", "US", "Food Delivery"), ("MERCH_0014", "Metro Transit", "LOW", "DE", "Transport"),
 ("MERCH_0015", "Aurora Cinemas", "LOW", "AU", "Entertainment"), ("MERCH_0016", "Harbor Retail", "LOW", "CA", "Retail"),
 ("MERCH_0017", "Helios Energy", "LOW", "FR", "Utilities"), ("MERCH_0018", "Sunrise Pharmacy", "LOW", "GB", "Pharmacy"),
 ("MERCH_0019", "StreamBox", "LOW", "US", "Streaming"), ("MERCH_0020", "Lumen Telecom", "LOW", "SG", "Telecom")]
MERCH_RISK = {m[0]: m[2] for m in MERCH}
LOW_M = [m[0] for m in MERCH if m[2] == "LOW"]; MED_M = [m[0] for m in MERCH if m[2] == "MEDIUM"]; HIGH_M = [m[0] for m in MERCH if m[2] == "HIGH"]

FIRST = ["Amelia", "Mateo", "Ingrid", "Kenji", "Fatima", "Liam", "Chloe", "Noah", "Priyanka", "Tomas", "Leila", "Oskar", "Marisol", "Dmitri", "Aiko", "Callum", "Zainab", "Henrik", "Camila", "Rafael",
         "Sunita", "Anders", "Yara", "Felix", "Nadia", "Joaquin", "Freya", "Idris", "Mei", "Tobias", "Elena", "Kwame", "Saoirse", "Arjun", "Beatriz", "Lars", "Imani", "Pavel", "Hana", "Ciaran",
         "Valeria", "Emeka", "Sofia", "Rohan", "Astrid", "Hugo", "Layla", "Bruno", "Mina", "Gideon", "Thea", "Omar", "Katya", "Jonas", "Amara", "Silas", "Noor", "Elias"]
LAST = ["Hartwell", "Rivas", "Solberg", "Aoyama", "Al-Rashidi", "Fairbanks", "Marchetti", "Whitcombe", "Venkataraman", "Brzezinski", "Haddad", "Lindqvist", "Okonkwo", "Petrenko", "Nakashima", "Drummond",
        "Abubakar", "Eriksen", "Montoya", "Castellano", "Raghunathan", "Holmgren", "Mansour", "Brandt", "Qureshi", "Villanueva", "Thorsen", "Balogun", "Chen-Whitfield", "Falkner", "Dragomir", "Asante",
        "Gallagher-Moss", "Iyengar", "Carvalho", "Ostrowski", "Adeyemi", "Kovalenko", "Yamada", "Donnelly", "Sandoval", "Eze", "Ricci", "Bhattacharya", "Lund", "Moreau", "Siddiqui", "Esposito", "Kimura", "Oyelaran",
        "Strand", "Khalil", "Morozov", "Vance", "Nwosu", "Harcourt", "Basu", "Lindgren"]
OCC = ["Software Engineer", "Teacher", "Doctor", "Accountant", "Nurse", "Engineer", "HR Manager", "Developer", "Designer", "Architect", "Pharmacist", "Consultant", "Electrician", "Chef", "Lawyer", "Analyst"]

def fx(ccy, usd): return round(usd / FX[ccy], 2)

# ------------------------------------------------------------------ customers
C = []  # dicts
def add(cid, name, cc, level, score, flag, occupation, income, years, email_domain="inbox.example", city=None, tag="generic"):
    cur, code, cities = COUNTRY[cc]
    local = name.lower().replace(" ", ".").replace("-", "")
    C.append(dict(id=cid, name=name, email=f"{local}@{email_domain}", phone=f"+{code} 555 {int(cid[-4:]):04d}", country=cc, level=level, score=score, flag=flag,
                  city=city or rng.choice(cities), occ=occupation, income=income, created=ASOF - timedelta(days=int(years * 365)), tag=tag, ccy=cur))

used = set()
def fresh_name():
    while True:
        n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if n not in used: used.add(n); return n
def named(n): used.add(n); return n

countries = list(COUNTRY)
for k in range(1, 10):   # low risk
    add(f"CUST_{k:04d}", fresh_name(), countries[(k - 1) % 8], "LOW", round(rng.uniform(0.02, 0.05), 3), False, rng.choice(OCC), rng.choice([3500, 4200, 5200, 6100, 7000, 8800]), rng.choice([1, 2, 3, 4, 5, 6]), tag="low")
for k in range(10, 15):  # medium
    add(f"CUST_{k:04d}", fresh_name(), countries[k % 8], "MEDIUM", round(rng.uniform(0.35, 0.49), 3), False, rng.choice(["Trader", "Freelancer", "Business owner", "Jeweller", "Consultant"]), rng.choice([9000, 12000, 15000, 18000]), rng.choice([1, 2, 3]),
        email_domain=("tempmail.example" if k == 10 else "postbox.example"), tag="medium")
add("CUST_0017", named("Rasmus Valdivia"), "US", "HIGH", 0.76, True, "Online seller", 6000, 2, tag="chargeback")
add("CUST_0019", named("Odalys Brennecke"), "DE", "MEDIUM", 0.52, False, "Shop owner", 7500, 3, tag="round")
add("CUST_0030", named("Garrick Ionescu"), "GB", "HIGH", 0.78, True, "Unemployed", 0, 1, email_domain="fakemail.example", tag="mule")
add("CUST_0031", named("Suvi Okeke"), "GB", "HIGH", 0.80, True, "Student", 800, 1, email_domain="fakemail.example", tag="mule")
add("CUST_0032", named("Dinesh Valmont"), "AE", "CRITICAL", 0.88, True, "Unknown", 0, 1, email_domain="throwaway.example", tag="mule")
add("CUST_0042", named("Marcus Vellacott"), "GB", "HIGH", 0.88, True, "Unknown", 0, 1, email_domain="tempmail.example", tag="primary")
for cid, n, cc in (("CUST_0099", "Corvin Aldrete", "GB"), ("CUST_0156", "Lakshmi Ferreiro", "GB"), ("CUST_0201", "Ansgar Delacroix-Pike", "DE"), ("CUST_0202", "Thandiwe Oyelowo", "GB"), ("CUST_0203", "Bastien Karlsson", "FR"), ("CUST_0204", "Yusuf Rzewuski", "GB")):
    add(cid, named(n), cc, "HIGH", round(rng.uniform(0.62, 0.80), 3), True, "Unknown", 0, 1, email_domain="fakemail.example", tag="ring")
for k in range(33, 51):  # mixed filler customers
    if k in (42,): continue
    lvl, sc = ("MEDIUM", round(rng.uniform(0.36, 0.55), 3)) if k % 4 == 0 else ("LOW", round(rng.uniform(0.02, 0.15), 3))
    add(f"CUST_{k:04d}", fresh_name(), countries[k % 8], lvl, sc, False, rng.choice(OCC), rng.choice([4000, 5500, 7000, 9500]), rng.choice([1, 2, 3, 4]), tag="medium" if lvl == "MEDIUM" else "low")
add("CUST_0055", fresh_name(), "SG", "MEDIUM", 0.44, False, "Consultant", 9000, 2, tag="medium")      # medium signals must not escalate
add("CUST_0300", named("Aleksander Brandt-Navarro"), "AE", "CRITICAL", 0.98, True, "Public official (fictional)", 14000, 5, email_domain="postbox.example", tag="pep")
for k, (n, cc, inc) in enumerate((("Kavya Rathnayake", "SG", 2800), ("Marlon Beaufort", "US", 3100), ("Ximena Aldana", "CA", 2600), ("Soren Wikstrom", "GB", 3400)), 1):
    add(f"CUST_030{k}", named(n), cc, "MEDIUM", round(rng.uniform(0.50, 0.60), 3), False, "Content creator", inc, 2, tag="income")
for k in range(60, 65):
    add(f"CUST_{k:04d}", fresh_name(), countries[k % 8], "LOW", round(rng.uniform(0.02, 0.08), 3), False, rng.choice(OCC), rng.choice([4500, 6000, 7500]), rng.choice([2, 3, 4]), tag="low")
BY = {c["id"]: c for c in C}

# ------------------------------------------------------------------ transactions
T = []   # dicts
CB = []
tid = [0]
def ip_for(cid, kind="home"):
    n = int(cid[-4:]); block = ("203.0.113", "198.51.100", "192.0.2")[n % 3]
    return f"{block}.{(n * 7) % 180 + 20}" if kind == "home" else f"198.51.100.{200 + n % 20}"
def dev_for(cid): return f"DEV_{cid[-4:]}_01"

def txn(cid, merchant, usd, when, country=None, channel="online", device=None, ip=None, status="SUCCESS", typ="DEBIT"):
    c = BY[cid]; tid[0] += 1
    T.append(dict(id=tid[0], cid=cid, m=merchant, usd=round(usd, 2), country=country or c["country"], channel=channel, device=device or dev_for(cid), ip=ip or ip_for(cid), when=when, status=status, typ=typ, ccy=c["ccy"]))
    return tid[0]

def when_spread(days=365): return ASOF - timedelta(days=rng.uniform(1, days), hours=rng.uniform(0, 23), minutes=rng.uniform(0, 59))
def amt(lo, hi): return round(rng.uniform(lo, hi), 2)

for c in C:
    cid, tag = c["id"], c["tag"]
    if tag == "low":
        for _ in range(rng.randint(50, 80)): txn(cid, rng.choice(LOW_M), amt(5, 420) * (3 if rng.random() < 0.05 else 1), when_spread())
    elif tag == "medium":
        for _ in range(rng.randint(60, 80)):
            r = rng.random()
            if r < 0.20: txn(cid, rng.choice(MED_M), amt(80, 1800), when_spread())
            elif r < 0.26: txn(cid, rng.choice(LOW_M), amt(40, 900), when_spread(), country=rng.choice([x for x in COUNTRY if x != c["country"]]))
            else: txn(cid, rng.choice(LOW_M), amt(8, 600), when_spread())
    elif tag == "chargeback":
        ids = [txn(cid, rng.choice(LOW_M + MED_M), amt(30, 700), when_spread()) for _ in range(120)]
        for i in rng.sample(ids, 30): CB.append(dict(cid=cid, txn=i, usd=T[i - 1]["usd"], reason=rng.choice(["Item not received", "Unauthorized transaction", "Not as described"]), status=rng.choice(["OPEN", "LOST", "WON"])))
    elif tag == "round":
        for _ in range(60):
            if rng.random() < 0.7: txn(cid, rng.choice(LOW_M + MED_M), float(rng.choice([1000, 2000, 5000, 10000, 3000])), when_spread())
            else: txn(cid, rng.choice(LOW_M), amt(20, 700), when_spread())
    elif tag == "mule":
        for _ in range(30): txn(cid, rng.choice(["MERCH_0002", "MERCH_0001"] + LOW_M[:3]), amt(1500, 9000), when_spread(120), device=f"DEV_MULE_00{int(cid[-1]) + 1}" if cid != "CUST_0030" else "DEV_MULE_001")
    elif tag == "ring":
        for _ in range(rng.randint(25, 40)): txn(cid, "MERCH_0001" if rng.random() < 0.8 else rng.choice(LOW_M), amt(800, 4200), when_spread(200), device="DEV_RING_SHARED_001", ip="203.0.113.99")
        for i in rng.sample(range(1, 40), 4):
            mine = [t for t in T if t["cid"] == cid]
            if mine: t = mine[i % len(mine)]; CB.append(dict(cid=cid, txn=t["id"], usd=t["usd"], reason="Unauthorized transaction", status="OPEN"))
    elif tag == "pep":
        for _ in range(40): txn(cid, rng.choice(HIGH_M + LOW_M[:2]), amt(50000, 250000), when_spread(300), country=rng.choice(["AE", "MT", "SG"]))
        mine = [t for t in T if t["cid"] == cid][:4]
        for t in mine: CB.append(dict(cid=cid, txn=t["id"], usd=t["usd"], reason="Dispute", status="OPEN"))
    elif tag == "income":
        for _ in range(70): txn(cid, rng.choice(["MERCH_0007", "MERCH_0009", "MERCH_0012", "MERCH_0019"] + LOW_M[:3]), amt(300, 4500), when_spread())
    elif tag == "primary":
        base = ASOF - timedelta(days=21, hours=5)
        for i in range(60): txn(cid, "MERCH_0001" if i % 2 else "MERCH_0002", amt(200, 900), base + timedelta(minutes=3 * i), device="DEV_RING_SHARED_001", ip="203.0.113.99")     # velocity burst
        lo, hi = RULES["GB"][0], RULES["GB"][1]
        for d in range(4):
            day = ASOF - timedelta(days=60 + 9 * d, hours=3)
            for j in range(rng.randint(3, 6)): txn(cid, rng.choice(["MERCH_0001", "MERCH_0004"]), amt(lo + 50, hi - 20), day + timedelta(minutes=14 * j), device="DEV_RING_SHARED_001", ip="203.0.113.99")   # structuring
        for _ in range(24): txn(cid, rng.choice(["MERCH_0002", "MERCH_0003", "MERCH_0004"]), amt(500, 6000), when_spread(150), country=rng.choice(["AE", "SG", "US"]), ip="198.51.100.205", device="DEV_CUST0042_MOBILE")  # cross-border
        for _ in range(80): txn(cid, rng.choice(LOW_M + MED_M), amt(15, 700), when_spread(), device="DEV_CUST0042_MOBILE")
        mine = [t for t in T if t["cid"] == cid]
        for t in rng.sample(mine, 12): CB.append(dict(cid=cid, txn=t["id"], usd=t["usd"], reason="Unauthorized transaction", status="OPEN"))

# ------------------------------------------------------------------ SQL
def q(s): return "'" + str(s).replace("'", "''") + "'"
def ts(d): return q(d.strftime("%Y-%m-%d %H:%M:%S"))
S = []
w = S.append
w("-- Verixa Bank: synthetic seed data (generated by data/generate.py, seed %d). FICTIONAL: no real person, company, address or number." % SEED)
w("DROP TABLE IF EXISTS chargebacks CASCADE;\nDROP TABLE IF EXISTS transactions CASCADE;\nDROP TABLE IF EXISTS customers CASCADE;\nDROP TABLE IF EXISTS merchants CASCADE;\nDROP TABLE IF EXISTS jurisdiction_rules CASCADE;")
w("""CREATE TABLE jurisdiction_rules (
    country_code          CHAR(2) PRIMARY KEY,
    structuring_low       DECIMAL(12,2) NOT NULL,
    structuring_high      DECIMAL(12,2) NOT NULL,
    structuring_min_count INTEGER NOT NULL,
    chargeback_limit_pct  DECIMAL(5,2) NOT NULL,
    note                  TEXT
);
CREATE TABLE customers (
    customer_id   VARCHAR(20) PRIMARY KEY,
    name          VARCHAR(100),
    email         VARCHAR(100),
    phone         VARCHAR(30),
    home_country  CHAR(2)      DEFAULT 'US',
    risk_level    VARCHAR(10)  DEFAULT 'LOW',
    risk_score    DECIMAL(4,3) DEFAULT 0.000,
    is_flagged    BOOLEAN      DEFAULT FALSE,
    city          VARCHAR(50),
    occupation    VARCHAR(50),
    monthly_income DECIMAL(12,2),
    created_at    TIMESTAMP    DEFAULT NOW()
);
CREATE TABLE merchants (
    merchant_id    VARCHAR(20) PRIMARY KEY,
    merchant_name  VARCHAR(100),
    risk_category  VARCHAR(10)  DEFAULT 'LOW',
    country_code   CHAR(2),
    category       VARCHAR(50),
    created_at     TIMESTAMP    DEFAULT NOW()
);
CREATE TABLE transactions (
    txn_id            SERIAL PRIMARY KEY,
    customer_id       VARCHAR(20) REFERENCES customers(customer_id),
    merchant_id       VARCHAR(20) REFERENCES merchants(merchant_id),
    amount            DECIMAL(12,2) NOT NULL,           -- reporting currency (USD)
    currency          CHAR(3)       DEFAULT 'USD',      -- currency of the column above
    original_amount   DECIMAL(14,2),                    -- amount in the customer's own currency
    original_currency CHAR(3),
    country_code      CHAR(2),
    channel           VARCHAR(20)   DEFAULT 'online',
    device_id         VARCHAR(30),
    ip_address        VARCHAR(20),
    txn_timestamp     TIMESTAMP     DEFAULT NOW(),
    status            VARCHAR(20)   DEFAULT 'SUCCESS',
    txn_type          VARCHAR(20)   DEFAULT 'DEBIT'
);
CREATE TABLE chargebacks (
    chargeback_id   SERIAL PRIMARY KEY,
    customer_id     VARCHAR(20) REFERENCES customers(customer_id),
    txn_id          INTEGER REFERENCES transactions(txn_id),
    amount          DECIMAL(12,2),
    reason          VARCHAR(100),
    status          VARCHAR(20) DEFAULT 'OPEN',
    created_at      TIMESTAMP   DEFAULT NOW()
);
CREATE INDEX idx_txn_customer ON transactions(customer_id);
CREATE INDEX idx_txn_merchant ON transactions(merchant_id);
CREATE INDEX idx_txn_timestamp ON transactions(txn_timestamp);
CREATE INDEX idx_txn_amount ON transactions(amount);
CREATE INDEX idx_txn_device ON transactions(device_id);
CREATE INDEX idx_chargeback_cust ON chargebacks(customer_id);""")
w("-- ILLUSTRATIVE near-threshold bands. Not legal values; ask the bank's compliance team for the real ones.")
w("INSERT INTO jurisdiction_rules VALUES")
w(",\n".join("(%s,%s,%s,%d,%s,%s)" % (q(k), v[0], v[1], v[2], v[3], q("illustrative value")) for k, v in RULES.items()) + ";")
w("INSERT INTO merchants (merchant_id, merchant_name, risk_category, country_code, category) VALUES")
w(",\n".join("(%s,%s,%s,%s,%s)" % tuple(q(x) for x in m) for m in MERCH) + ";")
w("INSERT INTO customers (customer_id,name,email,phone,home_country,risk_level,risk_score,is_flagged,city,occupation,monthly_income,created_at) VALUES")
w(",\n".join("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)" % (q(c["id"]), q(c["name"]), q(c["email"]), q(c["phone"]), q(c["country"]), q(c["level"]), c["score"], "TRUE" if c["flag"] else "FALSE", q(c["city"]), q(c["occ"]), c["income"], ts(c["created"])) for c in C) + ";")
for i in range(0, len(T), 400):
    w("INSERT INTO transactions (txn_id,customer_id,merchant_id,amount,currency,original_amount,original_currency,country_code,channel,device_id,ip_address,txn_timestamp,status,txn_type) VALUES")
    w(",\n".join("(%d,%s,%s,%s,'USD',%s,%s,%s,%s,%s,%s,%s,%s,%s)" % (t["id"], q(t["cid"]), q(t["m"]), t["usd"], fx(t["ccy"], t["usd"]), q(t["ccy"]), q(t["country"]), q(t["channel"]), q(t["device"]), q(t["ip"]), ts(t["when"]), q(t["status"]), q(t["typ"])) for t in T[i:i + 400]) + ";")
w("SELECT setval(pg_get_serial_sequence('transactions','txn_id'), (SELECT MAX(txn_id) FROM transactions));")
w("INSERT INTO chargebacks (customer_id,txn_id,amount,reason,status,created_at) VALUES")
w(",\n".join("(%s,%d,%s,%s,%s,%s)" % (q(x["cid"]), x["txn"], x["usd"], q(x["reason"]), q(x["status"]), ts(ASOF - timedelta(days=rng.randint(2, 90)))) for x in CB) + ";")
open(os.path.join(OUT, "seed.sql"), "w").write("\n".join(S) + "\n")

# ------------------------------------------------------------------ Neo4j (from the same data)
G = []
g = G.append
g("MATCH (n) DETACH DELETE n;")
devices = {}; ips = {}
for t in T:
    devices.setdefault(t["device"], set()).add(t["cid"]); ips.setdefault(t["ip"], set()).add(t["cid"])
SUSP_DEV = {"DEV_RING_SHARED_001", "DEV_MULE_001", "DEV_MULE_002", "DEV_MULE_003", "DEV_DARK_001"}
for d in sorted(devices):
    g("CREATE (:Device {device_id: %s, device_type: %s, is_suspicious: %s});" % (q(d), q(rng.choice(["Android", "iPhone", "Laptop"])), "true" if d in SUSP_DEV else "false"))
for ip in sorted(ips):
    vpn = ip.startswith("198.51.100.2")
    g("CREATE (:IPAddress {ip: %s, is_vpn: %s, risk_score: %s});" % (q(ip), "true" if vpn else "false", 0.9 if vpn else (0.8 if ip == "203.0.113.99" else 0.2)))
for m in MERCH:
    g("CREATE (:Merchant {entity_id: %s, name: %s, risk_level: %s, country: %s, category: %s, is_flagged: %s});" % (q(m[0]), q(m[1]), q(m[2]), q(m[3]), q(m[4]), "true" if m[2] == "HIGH" else "false"))
for c in C:
    g("CREATE (:Customer {entity_id: %s, name: %s, email: %s, phone: %s, risk_level: %s, risk_score: %s, is_flagged: %s, city: %s});" % (q(c["id"]), q(c["name"]), q(c["email"]), q(c["phone"]), q(c["level"]), c["score"], "true" if c["flag"] else "false", q(c["city"])))
BANKS = ["Verixa Bank", "Northgate Union", "Crescent Savings"]
acc_n = 0
for c in C:
    for b in range(1 if c["level"] == "LOW" else 2):
        acc_n += 1; aid = f"ACC_{c['id'][-4:]}_{b + 1}"
        g("CREATE (:Account {account_id: %s, customer_id: %s, bank: %s, is_flagged: %s, balance: %d});" % (q(aid), q(c["id"]), q(BANKS[(acc_n + b) % 3]), "true" if c["flag"] else "false", rng.randint(500, 90000)))
        g("MATCH (c:Customer {entity_id:%s}), (a:Account {account_id:%s}) CREATE (c)-[:HAS_ACCOUNT]->(a);" % (q(c["id"]), q(aid)))
g("CREATE (:Account {account_id: 'ACC_FLAGGED_END', customer_id: 'CUST_0032', bank: 'Crescent Savings', is_flagged: true, balance: 410000});")
g("MATCH (c:Customer {entity_id:'CUST_0032'}), (a:Account {account_id:'ACC_FLAGGED_END'}) CREATE (c)-[:CONTROLS {hidden:true}]->(a);")
agg = {}
for t in T:
    k = (t["cid"], t["m"]); a = agg.setdefault(k, [0, 0.0]); a[0] += 1; a[1] += t["usd"]
# The graph keeps only the relationships that matter to fraud: edges to HIGH-risk merchants. The table remains the full record.
# (Loading every merchant would link clean customers to rings merely because they shop at the same supermarket.)
for (cid, m), (n, tot) in sorted(agg.items()):
    if MERCH_RISK[m] != "HIGH": continue
    g("MATCH (c:Customer {entity_id:%s}), (m:Merchant {entity_id:%s}) CREATE (c)-[:TRANSACTED_WITH {count:%d, total_amount:%d, flagged:%s}]->(m);" % (q(cid), q(m), n, int(tot), "true" if MERCH_RISK[m] == "HIGH" else "false"))
dev_use = {}; ip_use = {}
for t in T:
    dev_use[(t["cid"], t["device"])] = dev_use.get((t["cid"], t["device"]), 0) + 1; ip_use[(t["cid"], t["ip"])] = ip_use.get((t["cid"], t["ip"]), 0) + 1
for (cid, d), n in sorted(dev_use.items()):
    g("MATCH (c:Customer {entity_id:%s}), (d:Device {device_id:%s}) CREATE (c)-[:USES_DEVICE {times:%d}]->(d);" % (q(cid), q(d), n))
for (cid, ip), n in sorted(ip_use.items()):
    g("MATCH (c:Customer {entity_id:%s}), (i:IPAddress {ip:%s}) CREATE (c)-[:USES_IP {times:%d}]->(i);" % (q(cid), q(ip), n))
for a, b_, amt_, n, how in (("CUST_0030", "CUST_0031", 120000, 18, "wire"), ("CUST_0031", "CUST_0032", 98000, 15, "wire")):
    g("MATCH (a:Customer {entity_id:%s}), (b:Customer {entity_id:%s}) CREATE (a)-[:TRANSFERRED_TO {amount:%d, times:%d, method:%s, flagged:true}]->(b);" % (q(a), q(b_), amt_, n, q(how)))
ring = ["CUST_0042", "CUST_0099", "CUST_0156", "CUST_0201", "CUST_0202", "CUST_0203", "CUST_0204"]
for i in range(len(ring) - 1):
    g("MATCH (a:Customer {entity_id:%s}), (b:Customer {entity_id:%s}) CREATE (a)-[:LINKED_TO {reason:'shared_device_and_address', strength:0.8}]->(b);" % (q(ring[i]), q(ring[i + 1])))
for a, b_ in (("CUST_0301", "CUST_0302"), ("CUST_0302", "CUST_0303"), ("CUST_0303", "CUST_0304")):
    g("MATCH (a:Customer {entity_id:%s}), (b:Customer {entity_id:%s}) CREATE (a)-[:LINKED_TO {reason:'business_partner_network', strength:0.6}]->(b);" % (q(a), q(b_)))
open(os.path.join(OUT, "neo4j_seed.cypher"), "w").write("\n".join(G) + "\n")


# ------------------------------------------------------------------ recorded fixtures for sample mode (all fictional)
FIX = os.path.join(HERE, "fixtures"); os.makedirs(FIX, exist_ok=True)
OUTLET = "The Daily Ledger (fictional)"
def hit(title, snippet, pos): return dict(title=title, snippet=snippet, link="https://news.example/" + hashlib.md5(title.encode()).hexdigest()[:8], displayed_link="news.example", position=pos)
ADVERSE = {
 "CUST_0042": {"fraud scam arrested": [hit("Remittance agent questioned in fraud inquiry", "Marcus Vellacott was named in a fraud inquiry into rapid transfers through two remittance firms.", 1), hit("Fraud ring suspected behind burst of small payments", "Investigators say several accounts shared one device and one agent, Marcus Vellacott among them.", 2)],
               "court case lawsuit": [hit("Lawsuit over unauthorised card payments", "A lawsuit names Marcus Vellacott in a dispute about unauthorised card payments.", 1)],
               "money laundering financial crime": [hit("Money laundering probe widens to remittance firms", "The probe examines whether structured payments were used to avoid reporting.", 1)]},
 "CUST_0300": {"fraud scam arrested": [hit("Official's offshore holdings questioned", "Aleksander Brandt-Navarro, a senior public official (fictional), faces questions about offshore holdings.", 1)],
               "court case lawsuit": [hit("Anti-corruption body opens case", "A case concerning undeclared assets was opened against Aleksander Brandt-Navarro.", 1)],
               "money laundering financial crime": [hit("Financial crime unit reviews casino and FX payments", "Unusual payments to a casino and a foreign-exchange firm are under review.", 1)]},
 "CUST_0030": {"fraud scam arrested": [hit("Student account used to pass on funds", "Garrick Ionescu was reported as a possible money mule in a bank alert.", 1)]},
 "CUST_0017": {"court case lawsuit": [hit("Online seller faces refund complaints", "Many buyers filed complaints against the shop run by Rasmus Valdivia.", 1)]},
}
serp = {BY[k]["name"]: dict(queries=v) for k, v in ADVERSE.items()}
json.dump(serp, open(os.path.join(FIX, "serp.json"), "w"), indent=1)
breach = {BY[k]["email"]: ["ForumDump 2022", "CredStuff collection"] for k in ("CUST_0042", "CUST_0099", "CUST_0156", "CUST_0030", "CUST_0031", "CUST_0032", "CUST_0010")}
json.dump(breach, open(os.path.join(FIX, "breach.json"), "w"), indent=1)
COMP = ["Brightwave Systems", "Harbor Health Group", "Kestrel Logistics", "Oakline Partners", "Quill & Hart", "Redfern Engineering", "Sable Analytics", "Tidewater Foods"]
NOPROFILE = {"CUST_0030", "CUST_0031", "CUST_0032", "CUST_0042", "CUST_0099", "CUST_0156", "CUST_0201", "CUST_0202", "CUST_0203", "CUST_0204"}
linkedin = {}
for c in C:
    if c["id"] in NOPROFILE: continue
    creator = c["tag"] == "income"; pep = c["tag"] == "pep"
    linkedin[c["name"]] = [dict(fullName=c["name"], headline=("Content creator and educator" if creator else ("Senior public official" if pep else f"{c['occ']} at {COMP[int(c['id'][-2:]) % 8]}")),
        connectionsCount=(rng.randint(4000, 9000) if creator else (1800 if pep else rng.randint(150, 900))),
        positions=[dict(title=("Creator" if creator else c["occ"]), companyName=("Independent" if creator else COMP[int(c["id"][-2:]) % 8]))], educations=[dict(schoolName="State University")], email="")]
json.dump(linkedin, open(os.path.join(FIX, "linkedin.json"), "w"), indent=1)
youtube = {}
for k, (subs, views, vids) in {"CUST_0301": (4200000, 640000000, 420), "CUST_0302": (2600000, 410000000, 380), "CUST_0303": (5100000, 820000000, 560), "CUST_0304": (1400000, 230000000, 210)}.items():
    youtube[BY[k]["name"]] = [dict(channelName=BY[k]["name"].split()[0] + " Learns", numberOfSubscribers=subs, numberOfViews=views, numberOfVideos=vids)]
json.dump(youtube, open(os.path.join(FIX, "youtube.json"), "w"), indent=1)
google = {}
for k in ("CUST_0301", "CUST_0302", "CUST_0303", "CUST_0304"):
    google[BY[k]["name"]] = [dict(organicResults=[dict(title=BY[k]["name"] + " buys a luxury penthouse", description="The creator shows a luxury penthouse and a supercar in a new video about success.")])]
google[BY["CUST_0300"]["name"]] = [dict(organicResults=[dict(title="Tax investigation into official's undeclared assets", description="A tax investigation concerns undeclared assets linked to Aleksander Brandt-Navarro; a shell company is mentioned.")])]
json.dump(google, open(os.path.join(FIX, "google.json"), "w"), indent=1)

man = dict(seed=SEED, as_of=str(ASOF), customers=len(C), merchants=len(MERCH), transactions=len(T), chargebacks=len(CB), devices=len(devices), ip_addresses=len(ips), accounts=acc_n + 1,
           graph_statements=len(G), seed_sql_sha256=hashlib.sha256(open(os.path.join(OUT, "seed.sql"), "rb").read()).hexdigest()[:16],
           note="Fictional data. Illustrative thresholds. Foreign-exchange rates are illustrative.")
json.dump(man, open(os.path.join(OUT, "MANIFEST.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in man.items() if k not in ("note",)}))
