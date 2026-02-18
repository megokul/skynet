.PHONY: help install test test-all test-unit clean clean-data run-api run-bot dev-setup manual-check-api manual-check-e2e manual-check-delegate check-stale-paths check-control-boundary smoke format lint check

# Default target
help:
	@echo "SKYNET Development Commands"
	@echo "============================"
	@echo ""
	@echo "Setup:"
	@echo "  make install      - Install dependencies"
	@echo "  make dev-setup    - Complete development setup"
	@echo ""
	@echo "Testing:"
	@echo "  make test         - Run control-plane tests (fast)"
	@echo "  make test-all     - Run all remaining tests"
	@echo "  make test-unit    - Alias of control-plane tests"
	@echo ""
	@echo "Running:"
	@echo "  make run-api      - Start FastAPI service (dev)"
	@echo "  make run-bot      - Start OpenClaw Telegram bot runtime"
	@echo ""
	@echo "Manual Checks:"
	@echo "  make manual-check-api       - Hit running API endpoints"
	@echo "  make manual-check-e2e       - OpenClaw -> SKYNET integration check"
	@echo "  make manual-check-delegate  - SKYNET delegate skill check"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean        - Clean cache and temporary files"
	@echo "  make clean-data   - Clean test data"
	@echo ""
	@echo "Development:"
	@echo "  make format       - Format code (black)"
	@echo "  make lint         - Run linters"
	@echo "  make check-stale-paths - Fail on stale root path references"
	@echo "  make check-control-boundary - Enforce SKYNET control-plane boundaries"
	@echo "  make smoke        - Quick repo health checks"
	@echo "  make check        - Run all checks"

install:
	pip install -r requirements.txt

dev-setup: install
	@echo "Setting up development environment..."
	@python -c "from pathlib import Path; src=Path('.env.example'); dst=Path('.env'); (dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8'), print('Created .env file - please configure it')) if (src.exists() and not dst.exists()) else print('.env already exists or .env.example missing')"
	@echo "Development setup complete!"

test:
	@echo "Running control-plane tests..."
	python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_worker_registry.py -q

test-all:
	@echo "Running all remaining tests..."
	python -m pytest tests/ -v

test-unit:
	@echo "Running control-plane unit tests..."
	python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py -q

run-api:
	@echo "Starting SKYNET FastAPI service..."
	python scripts/dev/run_api.py

run-bot:
	@echo "Starting OpenClaw Telegram bot runtime..."
	python openclaw-gateway/main.py

manual-check-api:
	@echo "Running manual API checks against http://localhost:8000..."
	python scripts/manual/check_api.py

manual-check-e2e:
	@echo "Running manual OpenClaw -> SKYNET integration check..."
	python scripts/manual/check_e2e_integration.py

manual-check-delegate:
	@echo "Running manual SKYNET delegate skill check..."
	python scripts/manual/check_skynet_delegate.py

check-stale-paths:
	@echo "Checking for stale path references..."
	python scripts/ci/check_stale_paths.py

check-control-boundary:
	@echo "Checking SKYNET control-plane boundaries..."
	python scripts/ci/check_control_plane_boundary.py

smoke: check-stale-paths check-control-boundary
	@echo "Running smoke checks..."
	python scripts/dev/smoke.py

clean:
	@echo "Cleaning cache and temporary files..."
	@python -c "from pathlib import Path; import shutil; [shutil.rmtree(p, ignore_errors=True) for p in Path('.').rglob('__pycache__') if p.is_dir()]; [p.unlink(missing_ok=True) for p in Path('.').rglob('*.pyc') if p.is_file()]; shutil.rmtree('.pytest_cache', ignore_errors=True); print('Clean complete!')"

clean-data:
	@echo "Cleaning test data..."
	@python -c "from pathlib import Path; import shutil; [shutil.rmtree(p, ignore_errors=True) for p in Path('data').glob('test_*')] if Path('data').exists() else None; print('Test data cleaned!')"

format:
	@echo "Formatting code with black..."
	black skynet/ tests/ scripts/

lint:
	@echo "Running linters..."
	flake8 skynet/ tests/ scripts/ --max-line-length=100

check: clean test lint
	@echo "All checks passed!"
