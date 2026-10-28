# One-command setup for macOS and Linux (Windows: use WSL, or run the commands in the README).
PY := .venv/bin/python
DC := docker compose

.PHONY: setup up seed run check test down reset
setup:            ## create the virtual environment and install the pinned libraries
	python3 -m venv .venv && $(PY) -m pip install -q --upgrade pip && $(PY) -m pip install -q -r requirements.txt
up:               ## start PostgreSQL and Neo4j and load both with the synthetic data
	$(DC) up -d --wait
	$(MAKE) seed
seed:             ## (re)load both databases from data/seed (regenerate with: python3 data/generate.py)
	$(DC) exec -T db psql -U fraud_user -d fraud_db -v ON_ERROR_STOP=1 -q < data/seed/seed.sql
	$(DC) exec -T db psql -U fraud_user -d fraud_db -v ON_ERROR_STOP=1 -q < backend/sql_file/multi-user-schema.sql
	$(DC) exec -T graph cypher-shell -u neo4j -p $${NEO4J_PASSWORD:-password} < data/seed/neo4j_seed.cypher > /dev/null
	@cat data/seed/MANIFEST.json | $(PY) -c "import sys,json; d=json.load(sys.stdin); print('seeded:', {k:d[k] for k in ('customers','merchants','transactions','chargebacks')})"
run:              ## start the API at http://localhost:8000
	$(PY) app/api.py
check:            ## a quick health check of the running API
	@curl -s localhost:8000/api/health; echo; curl -s localhost:8000/api/validate/CUST_0042; echo
test:             ## the unit tests (no model key needed)
	$(PY) -m pytest -q tests
down:             ## stop the databases (data is kept)
	$(DC) down
reset:            ## stop the databases and delete their data
	$(DC) down -v
