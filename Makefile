.PHONY: install test test-integration migrate load-demo gui ingest scan extract docker-up docker-down docker-logs clean help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package and dev dependencies (editable)
	pip install -e .[dev]

test:  ## Run the unit test suite (no database required)
	pytest tests/ -v -m "not integration" --ignore=tests/integration

test-integration:  ## Run integration tests against a Postgres service
	@echo "Bringing up postgres via docker-compose..."
	docker compose up -d postgres
	@echo "Waiting for Postgres to accept connections..."
	@for i in $$(seq 1 30); do \
	    docker compose exec -T postgres pg_isready -U throughline -d claude_memory > /dev/null 2>&1 && break || sleep 1; \
	done
	PGHOST=localhost PGPORT=5432 PGUSER=throughline PGPASSWORD=throughline_dev_password \
	    PGADMINDB=postgres pytest tests/integration/ -v -m integration

migrate:  ## Apply pending SQL migrations from sql/migrations/
	python3 scripts/migrate.py

load-demo:  ## Load the bundled demo dataset into claude_memory
	bash scripts/load_demo.sh

gui:  ## Start the Streamlit GUI
	streamlit run gui/app.py

ingest:  ## Ingest Claude Code JSONL sessions
	python -m throughline ingest

scan:  ## Scan skills + prompts
	python -m throughline scan-skills
	python -m throughline scan-prompts

extract:  ## Extract memory chunks via Claude CLI
	python -m throughline extract-memory

docker-up:  ## Start the Docker stack (Postgres + GUI)
	docker compose up -d

docker-down:  ## Stop the Docker stack
	docker compose down

docker-logs:  ## Tail Docker logs
	docker compose logs -f

clean:  ## Remove pycache and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/
