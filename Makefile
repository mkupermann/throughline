.PHONY: install venv test test-integration migrate load-demo serve ingest scan extract init-compose docker-up docker-down docker-logs clean help

# Resolve a Python 3.10+ interpreter for venv bootstrap (override with `make PYTHON=…`).
PYTHON ?= $(shell command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
VENV   ?= .venv
VPY    := $(VENV)/bin/python
VPIP   := $(VENV)/bin/pip
COMPOSE ?= docker compose
COMPOSE_ENV ?= .env

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

$(VPY):
	@if [ -z "$(PYTHON)" ]; then \
	  echo "No python3 interpreter found. Install Python 3.10+ first."; exit 1; \
	fi
	@ver=$$("$(PYTHON)" -c 'import sys; print("{}.{}".format(*sys.version_info[:2]))'); \
	major=$${ver%%.*}; minor=$${ver##*.}; \
	if [ "$$major" -lt 3 ] || { [ "$$major" = 3 ] && [ "$$minor" -lt 10 ]; }; then \
	  echo "Python $$ver is too old (need >= 3.10). Set PYTHON=/path/to/python3.10+ and retry."; exit 1; \
	fi
	"$(PYTHON)" -m venv $(VENV)
	$(VPIP) install --upgrade pip

venv: $(VPY)  ## Create the project virtualenv (.venv) using a Python >= 3.10

install: $(VPY)  ## Create venv and install the package + dev dependencies (editable)
	$(VPIP) install -e '.[dev]'
	@echo
	@echo "Virtualenv ready at $(VENV)/. Activate with: source $(VENV)/bin/activate"
	@echo "Scripts under scripts/ auto-bootstrap to $(VENV) — running them with system python3 also works."

test: $(VPY)  ## Run the unit test suite (no database required)
	$(VPY) -m pytest tests/ -v -m "not integration" --ignore=tests/integration

test-integration: $(VPY)  ## Run integration tests against a Postgres service
	"$(PYTHON)" scripts/init_compose_env.py --env-file "$(COMPOSE_ENV)"
	@echo "Bringing up PostgreSQL with the private Compose configuration..."
	$(COMPOSE) --env-file "$(COMPOSE_ENV)" up -d postgres
	@echo "Waiting for Postgres to accept connections..."
	@db=$$(sed -n 's/^POSTGRES_DB=//p' "$(COMPOSE_ENV)" | tail -1); \
	user=$$(sed -n 's/^POSTGRES_USER=//p' "$(COMPOSE_ENV)" | tail -1); \
	ready=0; \
	for i in $$(seq 1 30); do \
	  if $(COMPOSE) --env-file "$(COMPOSE_ENV)" exec -T postgres \
	      pg_isready -U "$$user" -d "$$db" > /dev/null 2>&1; then ready=1; break; fi; \
	  sleep 1; \
	done; \
	[ "$$ready" = 1 ] || { echo "PostgreSQL did not become ready" >&2; exit 1; }
	@user=$$(sed -n 's/^POSTGRES_USER=//p' "$(COMPOSE_ENV)" | tail -1); \
	password=$$(sed -n 's/^POSTGRES_PASSWORD=//p' "$(COMPOSE_ENV)" | tail -1); \
	port=$$(sed -n 's/^THROUGHLINE_DB_PORT=//p' "$(COMPOSE_ENV)" | tail -1); \
	PGHOST=127.0.0.1 PGPORT="$${port:-5433}" PGUSER="$$user" PGPASSWORD="$$password" \
	  PGADMINDB=postgres $(VPY) -m pytest tests/integration/ -v -m integration

migrate: $(VPY)  ## Apply pending packaged SQL migrations
	$(VPY) -m throughline migrate

load-demo:  ## Load the bundled demo dataset into the configured database
	bash scripts/load_demo.sh

serve: $(VPY)  ## Start the web UI + API on http://127.0.0.1:8790
	$(VPY) -m throughline serve

ingest: $(VPY)  ## Ingest sessions from every discovered adapter
	$(VPY) -m throughline ingest --all

scan: $(VPY)  ## Scan skills + prompts
	$(VPY) -m throughline scan-skills
	$(VPY) -m throughline scan-prompts

extract: $(VPY)  ## Extract memory chunks with the configured backend
	$(VPY) -m throughline extract-memory

init-compose:  ## Create/update private Compose credentials and host identity
	"$(PYTHON)" scripts/init_compose_env.py --env-file "$(COMPOSE_ENV)"

docker-up: init-compose  ## Start the Docker stack (Postgres + web UI)
	$(COMPOSE) --env-file "$(COMPOSE_ENV)" up -d

docker-down:  ## Stop the Docker stack
	$(COMPOSE) --env-file "$(COMPOSE_ENV)" down

docker-logs:  ## Tail Docker logs
	$(COMPOSE) --env-file "$(COMPOSE_ENV)" logs -f

clean:  ## Remove pycache and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/
