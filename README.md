# Verixa Bank: an AI fraud investigation assistant

A hands-on project in building and deploying an AI system inside a customer's environment. Verixa is a small, fictional global bank. Its fraud team needs one
view of a customer: transactions, relationships between accounts, and outside signals, with a score, a recommendation
and a report. You inherit a working but imperfect system and take it from your laptop to Google Cloud, then add the
monitoring, observability and compliance a bank needs.

> **Everything here is fictional.** Customers, merchants, e-mail addresses, phone numbers and IP addresses are invented
> (reserved `.example` domains, documentation IP ranges). Thresholds are illustrations, not legal values.
> This project gives no legal or compliance advice.

## Quick start (about five minutes)

You need: Docker Desktop, Python 3.11 or newer, and a free Gemini key from https://aistudio.google.com/apikey

```bash
git clone <this repository> && cd <folder>
cp .env.example .env          # put your Gemini key in GOOGLE_API_KEY; leave EXTERNAL_MODE=sample
make setup                    # virtual environment and the pinned libraries
make up                       # PostgreSQL and Neo4j in Docker, both loaded with the synthetic data
make run                      # the API and the screen at http://localhost:8000
make check                    # in a second terminal: health and one customer
```

Windows: use WSL, or run the commands of the Makefile one by one in PowerShell.

Demo logins (synthetic, for local use only): `admin@verixa.com` / `Admin@2026`, `reviewer1@verixa.com` / `Review1@2026`, `reviewer2@verixa.com` / `Review2@2026`.
Try customer `CUST_0042` (a designed fraud case) and `CUST_0001` (a clean customer).

## Sample mode and live mode

Outside services (web search, breach check, profile lookups) are **recorded examples about fictional people** while
`EXTERNAL_MODE=sample`: nothing leaves your machine, nothing costs money and no real person is looked up. `/api/health`
shows which mode is on. `EXTERNAL_MODE=live` calls the real services and needs their keys; use it only with a lawful
basis and the bank's approval. The code default is `live` on purpose, so a deployment can never fall back to sample
data by accident.

## What is in here

| Folder | What it is |
|---|---|
| `app/` `backend/` | the FastAPI service, the LangGraph agents, the MCP server, login with three roles, PDF reports |
| `frontend/` | the investigator's screen (one HTML file) |
| `data/` | `generate.py` (seeded generator, same files every run), `seed/` (PostgreSQL and Neo4j data), `fixtures/` (sample-mode examples) |
| `docker-compose.yml` `Makefile` | the databases and the one-command setup |
| `.github/workflows/deploy.yml` | the Google Cloud pipeline (used in the second half of the project) |
| `tests/` | checks that need no model key |
| `docs/` | project documents: discovery notes, the data and systems map, and later the design, decision records and runbook |

## How an investigation works

```
web check (always first)  ->  SQL checks and graph checks in parallel  ->  role-based agents  ->  risk synthesis
                                                                                      |
                              score, level, indicators, recommendation, report, SAR flag
```

Web check: search results, data-breach and disposable-e-mail checks. SQL checks (PostgreSQL): velocity, structuring,
cross-border, chargebacks, round amounts, risky merchants. Graph checks (Neo4j): shared devices, rings, mule chains.
Role agents: professional profile, tax and credit, online presence, income (depending on the user's role).

## API

| Method | Path | Needs login | What it does |
|---|---|---|---|
| POST | `/api/auth/login` | no | body `{"email","password"}`; returns a token |
| GET | `/api/auth/me` | yes | the current user |
| GET | `/api/health` | no | status and external mode |
| GET | `/api/validate/{entity_id}` | no | does the customer exist |
| POST | `/api/run` | optional | run an investigation: `{"entity_id","scope"}` with scope `full` or `quick` |
| POST | `/api/pdf/generate` | yes | PDF report for an investigation |
| GET | `/api/pdf/my-reports` | yes | your reports |
| GET | `/api/admin/sessions`, `/api/admin/pdfs` | admin | all sessions and reports |
| POST | `/api/eval/run`, `/api/eval/start`; GET `/api/eval/stream/{job_id}` | yes | model-judged evaluation (costs tokens) |

## The synthetic data

54 customers in eight countries, 20 merchants, about 3,400 transactions in several currencies (reported in USD),
about 70 chargebacks. Designed cases: `CUST_0042` (velocity, structuring, cross-border, ring), `CUST_0099` `CUST_0156`
`CUST_0201`-`0204` (the ring), `CUST_0030`-`0032` (a money-mule chain), `CUST_0017` (chargebacks), `CUST_0019` (round
amounts), `CUST_0300` (a high-profile, fictional official), `CUST_0301`-`0304` (income that does not match the
declared income). Change the data: edit `data/generate.py`, run `python3 data/generate.py`, then `make seed`.

## Honest limits

This is a demonstration project, not a production system. Do not use it with real customer data.
