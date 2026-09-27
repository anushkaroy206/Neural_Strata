# Neural Strata — the five commands anyone needs
# ==============================================
.PHONY: help run check test record replay db-init db-status schema sim clean

PY ?= python3
BACKEND := backend
PORT ?= 5000

help:
	@echo "make run        start the edge server (simulator if no hub attached)"
	@echo "make check      run both gates: contracts + published numbers"
	@echo "make test       pytest the backend suite"
	@echo "make db-init    apply pending migrations"
	@echo "make db-status  show the migration ledger and tables"
	@echo "make schema     regenerate persistence/schema.sql from the migrations"
	@echo "make record     record 60 s of a running server to recordings/"
	@echo "make replay     replay the newest recording at 10x"
	@echo "make sim        start the server with no serial and no blackbox"
	@echo "make clean      remove caches and pyc files"

run:
	cd $(BACKEND) && $(PY) app.py

check:
	cd $(BACKEND) && $(PY) tools/check_contracts.py && $(PY) tools/check_numbers.py

test:
	cd $(BACKEND) && $(PY) -m pytest tests/ -q

db-init:
	cd $(BACKEND) && $(PY) -m persistence.db --init

db-status:
	cd $(BACKEND) && $(PY) -m persistence.db --status

schema:
	cd $(BACKEND) && $(PY) -m persistence.db --dump-schema

record:
	cd $(BACKEND) && $(PY) tools/record.py --seconds 60 --tag make-run

replay:
	cd $(BACKEND) && $(PY) tools/replay.py "$$(ls -t ../recordings/*.csv | head -1)" --speed 10

sim:
	cd $(BACKEND) && $(PY) app.py --no-serial --no-blackbox

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	rm -rf $(BACKEND)/.pytest_cache
