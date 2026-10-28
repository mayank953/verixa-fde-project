# Data and systems map: Verixa Bank fraud assistant

| Dataset | Where it lives | Rows today | Owner and access (ask the customer) | Sensitivity |
|---|---|---|---|---|
| Customers | PostgreSQL table customers; nodes in Neo4j | 54 | ? | Personal data |
| Merchants | PostgreSQL table merchants | 20 | ? | Business data |
| Payments | PostgreSQL table transactions | 3,426 | ? | Financial and personal |
| Chargebacks | PostgreSQL table chargebacks | 70 | ? | Financial |
| Devices and IP addresses | Neo4j nodes; columns in payments | 49 and 50 | ? | Personal data |
| Accounts and links | Neo4j | 82 accounts | ? | Financial and personal |
| Country rules | PostgreSQL table jurisdiction_rules (illustrative) | 8 countries | ? | Internal |
| Outside signals | Search, breach and e-mail services (sample or live) | per request | ? | Leaves the bank |

Questions for the customer:
- Who owns each system, and who approves access?
- Which fields are personal data under the rules that apply?
- What may leave the bank, and what may not?
