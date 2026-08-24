# Lenny Growth Assistant — operator commands.
# `make help` lists everything. `make quickstart` is the path for a fresh clone.

SHELL := /bin/bash
PY := backend/.venv/bin/python
PIP := backend/.venv/bin/pip
TRANSCRIPT_REPO := https://github.com/ChatPRD/lennys-podcast-transcripts.git
TRANSCRIPT_DIR := data/transcripts

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------- docker
.PHONY: up
up: ## Build and start the whole stack (postgres + backend + frontend)
	docker compose up --build -d
	@echo "backend  → http://localhost:8000/health"
	@echo "frontend → http://localhost:3000"

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: clean
clean: ## Stop the stack and delete the database volume
	docker compose down -v

.PHONY: logs
logs: ## Tail all service logs
	docker compose logs -f --tail=100

.PHONY: ps
ps: ## Show service status
	docker compose ps

# ------------------------------------------------------------- transcripts
.PHONY: transcripts
transcripts: ## Clone the Lenny's Podcast transcript archive into data/transcripts
	@if [ -d "$(TRANSCRIPT_DIR)/episodes" ]; then \
		echo "Transcripts already present — pulling latest."; \
		cd $(TRANSCRIPT_DIR) && git pull --ff-only; \
	else \
		echo "Cloning $(TRANSCRIPT_REPO)…"; \
		git clone --depth 1 $(TRANSCRIPT_REPO) $(TRANSCRIPT_DIR)/_archive; \
		mv $(TRANSCRIPT_DIR)/_archive/episodes $(TRANSCRIPT_DIR)/episodes; \
		rm -rf $(TRANSCRIPT_DIR)/_archive; \
	fi
	@echo "Episodes: $$(ls $(TRANSCRIPT_DIR)/episodes | wc -l)"

# --------------------------------------------------------------- ingestion
.PHONY: ingest
ingest: ## Ingest the full corpus into the running stack (docker)
	docker compose exec backend python -m app.scripts.ingest

.PHONY: ingest-demo
ingest-demo: ## Ingest 25 episodes — enough for a demo, finishes in minutes
	docker compose exec backend python -m app.scripts.ingest --limit 25

.PHONY: ingest-local
ingest-local: ## Ingest using the local virtualenv instead of docker
	cd backend && .venv/bin/python -m app.scripts.ingest

.PHONY: corpus
corpus: ## Print what is currently indexed
	docker compose exec backend python -m app.scripts.ingest --stats

# ---------------------------------------------------------------- local dev
.PHONY: venv
venv: ## Create the backend virtualenv and install dependencies
	python3 -m venv backend/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements-dev.txt

.PHONY: migrate
migrate: ## Run database migrations against DATABASE_URL
	cd backend && .venv/bin/alembic upgrade head

.PHONY: dev-backend
dev-backend: ## Run the API with autoreload on :8000
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

.PHONY: dev-frontend
dev-frontend: ## Run the Next.js dev server on :3000
	cd frontend && npm run dev

# ------------------------------------------------------------------- tests
.PHONY: test
test: test-backend test-frontend ## Run every test suite

.PHONY: test-backend
test-backend: ## Backend tests (needs Postgres + pgvector on TEST_DATABASE_URL)
	cd backend && .venv/bin/python -m pytest

.PHONY: test-frontend
test-frontend: ## Frontend unit tests
	cd frontend && npm run test

.PHONY: typecheck
typecheck: ## TypeScript type check
	cd frontend && npm run typecheck

# -------------------------------------------------------------- quickstart
.PHONY: setup
setup: ## One command: prerequisites, models, transcripts, stack, knowledge base
	./scripts/setup.sh

.PHONY: setup-full
setup-full: ## Same as `setup` but indexes all 303 episodes instead of 25
	./scripts/setup.sh --full

.PHONY: quickstart
quickstart: transcripts up ## Transcripts + stack, then tells you what's next
	@echo ""
	@echo "Stack is starting. Next:"
	@echo "  1. ollama serve && ollama pull llama3.1:8b && ollama pull nomic-embed-text"
	@echo "  2. make ingest-demo"
	@echo "  3. open http://localhost:3000"
	@echo ""
	@echo "Or skip all of that: make setup  (Windows: .\scripts\setup.ps1)"
