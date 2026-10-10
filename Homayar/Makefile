.PHONY: help neo4j-up neo4j-down seed demo-seed backend frontend celery worker up down status repo-update repo-status repo-push

help: ## Show all commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

neo4j-up: ## Start Neo4j database
	docker compose up -d
	@echo "Neo4j Browser: http://localhost:7474"

neo4j-down: ## Stop Neo4j
	docker compose down

seed: ## Seed database with dev data
	.venv/bin/python -m backend.database.seed

demo-seed: ## Seed a demo SaaS workspace for the product console (development/test only)
	.venv/bin/python -m backend.database.demo_seed

backend: ## Start FastAPI server (port 8001)
	.venv/bin/uvicorn backend.main:app --reload --port 8001 --host 0.0.0.0

frontend: ## Start React dev server (port 5174)
	cd frontend && npx vite --port 5174 --host

celery: ## Start Celery worker
	.venv/bin/celery -A backend.celery_app worker --queues processing --loglevel=info --concurrency=2

worker: celery ## Alias for celery

redis: ## Start Redis container
	@docker start homayar-redis 2>/dev/null || docker run -d --name homayar-redis -p 6379:6379 redis:7-alpine

# ─── One-command startup ────────────────────────────────────

up: ## Start everything (Neo4j + Redis + Backend + Celery + Frontend)
	@echo "Starting Homayar..."
	@docker compose up -d 2>/dev/null || echo "  Docker: some containers may be missing"
	@docker start homayar-redis 2>/dev/null || docker run -d --name homayar-redis -p 6379:6379 redis:7-alpine 2>/dev/null || true
	@sleep 2
	@echo "  Starting backend (port 8001)..."
	@.venv/bin/uvicorn backend.main:app --reload --port 8001 --host 0.0.0.0 &\
		sleep 3 && echo "  Backend ready." &&\
		echo "  Starting Celery worker..." &&\
		.venv/bin/celery -A backend.celery_app worker --loglevel=warning --concurrency=2 &\
		sleep 2 && echo "  Celery ready." &&\
		echo "  Starting frontend (port 5174)..." &&\
		cd frontend && npx vite --port 5174 --host

down: ## Stop all services
	@echo "Stopping Homayar..."
	@pkill -f "celery.*backend.celery_app" 2>/dev/null; true
	@pkill -f "uvicorn backend.main" 2>/dev/null; true
	@pkill -f "vite.*5174" 2>/dev/null; true
	@docker stop homayar-redis 2>/dev/null; true
	@docker compose down 2>/dev/null; true
	@echo "All services stopped."

status: ## Check what's running
	@echo "=== Homayar Status ==="
	@echo -n "  Neo4j:   " && (docker compose ps 2>/dev/null | grep -q "Up" && echo "✓ running" || echo "✗ stopped")
	@echo -n "  Redis:   " && (docker exec homayar-redis redis-cli ping 2>/dev/null | grep -q PONG && echo "✓ running (port 6379)" || echo "✗ stopped")
	@echo -n "  Backend: " && (curl -s http://localhost:8001/api/health > /dev/null 2>&1 && echo "✓ running (port 8001)" || echo "✗ stopped")
	@echo -n "  Celery:  " && (pgrep -f "celery.*backend.celery_app" > /dev/null && echo "✓ running" || echo "✗ stopped")
	@echo -n "  Frontend:" && (curl -s http://localhost:5174 > /dev/null 2>&1 && echo "✓ running (port 5174)" || echo "✗ stopped")
	@echo ""
	@echo -n "  LLM Providers: " && curl -s http://localhost:8001/api/provider-status 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(d['available_providers']) or 'none')" 2>/dev/null || echo "backend not running"

# ─── Git / Repo ──────────────────────────────────────────────

repo-status: ## Show working tree status
	@echo "=== Branch ===" && git branch --show-current
	@echo "=== Status ===" && git status -s
	@echo "=== Last 5 commits ===" && git log --oneline -5 2>/dev/null || echo "  (no commits yet)"

repo-update: ## Stage all changes, commit, and push to dev branch
	@echo "Staging files..."
	@git add -A
	@if [ -z "$$(git status --porcelain)" ]; then \
		echo "Nothing to commit. Working tree clean."; \
		exit 0; \
	fi
	@echo "Committing..."
	@git commit -m "$${MSG:-dev: update $$(date +%Y-%m-%d\ %H:%M)}"
	@echo "Pushing to origin/dev..."
	@git push origin HEAD:dev 2>/dev/null || git push -u origin dev
	@echo "Done."

repo-push: ## Push current branch to origin
	@git push origin $$(git branch --show-current) 2>/dev/null || git push -u origin $$(git branch --show-current)
