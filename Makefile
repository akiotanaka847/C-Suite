.PHONY: dev stop test lint eval docker clean install discord

install:
	cd packages/core && uv sync
	cd packages/ui && npm install

dev:
	@echo "Starting Open Executive..."
	@cd packages/core && uv run uvicorn openexecutive.api.main:app --reload --port 8000 &
	@cd packages/ui && npm run dev

stop:
	@lsof -ti :8000 -ti :3000 2>/dev/null | xargs kill -9 2>/dev/null || true
	@echo "Stopped."

test:
	cd packages/core && uv run pytest tests/ -v --tb=short

lint:
	cd packages/core && uv run ruff check openexecutive/ && uv run mypy openexecutive/

eval:
	cd packages/core && uv run python ../../evals/run_evals.py \
		--scenarios ../../evals/scenarios/ \
		--output ../../evals/results/

docker:
	docker compose -f docker/docker-compose.yml up --build

docker-down:
	docker compose -f docker/docker-compose.yml down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf packages/core/.venv packages/core/.mypy_cache packages/core/.ruff_cache
	rm -rf packages/ui/node_modules packages/ui/.next

discord:
	cd packages/core && uv run python -m openexecutive.integrations.discord_bot

seed-knowledge:
	cd packages/core && uv run python -c "from openexecutive.knowledge.loader import seed_builtin_knowledge; import asyncio; asyncio.run(seed_builtin_knowledge())"
