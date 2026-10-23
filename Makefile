.PHONY: test test-demo lint format run clean build-sandbox demo-tools install dev

install:
	pip install -e ".[dev]"

dev: install

test:
	pytest tests/ -v --tb=short

test-demo:
	pytest demo_repo/tests/ -v --tb=short

lint:
	ruff check src/ tests/ demo_repo/ scripts/
	ruff format --check src/ tests/ demo_repo/ scripts/

format:
	ruff check --fix src/ tests/ demo_repo/ scripts/
	ruff format src/ tests/ demo_repo/ scripts/

build-sandbox:
	docker build -t repoagent-sandbox:latest sandbox/

demo-tools:
	python scripts/demo_tools.py

run:
	uvicorn src.repoagent.api:app --reload --port 8000

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	rm -rf dist/ build/ *.egg-info/
