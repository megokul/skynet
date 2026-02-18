.PHONY: help install test test-all clean run-tests run-api run-bot run-worker run-demo dev-setup manual-check-api manual-check-e2e manual-check-delegate check-stale-paths smoke

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
	@echo "  make test         - Run core tests (fast)"
	@echo "  make test-all     - Run all tests including integration"
	@echo "  make test-unit    - Run unit tests only"
	@echo "  make test-e2e     - Run end-to-end tests"
	@echo ""
	@echo "Running:"
	@echo "  make run-api      - Start FastAPI service (dev)"
	@echo "  make run-bot      - Start Telegram bot"
	@echo "  make run-worker   - Start Celery worker"
	@echo "  make run-demo     - Run interactive demo"
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
	@echo "  make check-stale-paths - Fail on deprecated root path references"
	@echo "  make smoke        - Quick repo health checks"
	@echo "  make check        - Run all checks"

install:
	pip install -r requirements.txt

dev-setup: install
	@echo "Setting up development environment..."
	@python -c "from pathlib import Path; src=Path('.env.example'); dst=Path('.env'); (dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8'), print('Created .env file - please configure it')) if (src.exists() and not dst.exists()) else print('.env already exists or .env.example missing')"
	@echo "Development setup complete!"

test:
	@echo "Running core tests..."
	python tests/test_planner_simple.py
	python tests/test_dispatcher.py
	python tests/test_orchestrator.py
	python tests/test_worker.py

test-all:
	@echo "Running all tests..."
	python -m pytest tests/ -v

test-unit:
	@echo "Running unit tests..."
	python tests/test_planner.py
	python tests/test_dispatcher.py
	python tests/test_orchestrator.py
	python tests/test_local_provider.py

test-e2e:
	@echo "Running end-to-end tests..."
	python tests/test_e2e.py
	python tests/test_worker.py

run-api:
	@echo "Starting SKYNET FastAPI service..."
	python scripts/dev/run_api.py

run-bot:
	@echo "Starting Telegram bot..."
	python scripts/run_telegram.py

run-worker:
	@echo "Starting Celery worker..."
	celery -A skynet.queue.worker worker --loglevel=info

run-demo:
	@echo "Starting interactive demo..."
	python scripts/run_demo.py

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

smoke: check-stale-paths
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
