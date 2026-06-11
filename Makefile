.PHONY: install lint format typecheck test test-unit test-integration bench bench-compare bench-multi bench-plot coverage ci clean

# Install dependencies
install:
	uv sync --all-extras

# Linting
lint:
	uv run ruff check src/ tests/

# Auto-fix lint issues
format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

# Type checking
typecheck:
	uv run mypy src/hedge/

# Run all tests
test: install
	uv run pytest tests/ -v --tb=short

# Unit tests only
test-unit: install
	uv run pytest tests/unit/ -v --tb=short

# Integration tests only
test-integration: install
	uv run pytest tests/integration/ -v --tb=short -m integration

# Benchmark tests
bench:
	uv run pytest tests/benchmark/ -v --benchmark-only --benchmark-sort=mean

# Benchmark comparison: no-hedge vs hedge configurations on httpx (outputs CSV + table)
bench-compare:
	uv run pytest tests/benchmark/test_bench_hedge_comparison.py -v -s --no-header -k test_full_comparison

# Multi-framework benchmark: httpx vs aiohttp vs grpc, no-hedge vs adaptive (outputs CSV + table)
bench-multi:
	uv run pytest tests/benchmark/test_bench_multi_framework.py -v -s --no-header -k test_multi_framework_comparison

# Generate evaluation charts (eval.png + eval_multi_framework.png) from benchmark CSVs
bench-plot:
	uv run python benchmark/plot.py

# Coverage report
coverage: install
	uv run pytest tests/ --cov=src/hedge --cov-report=term-missing --cov-report=html

# Run full CI checks locally
ci: lint typecheck test coverage

# Clean build artifacts
clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
