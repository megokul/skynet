.PHONY: help install install-agent install-all test test-all test-unit test-control-plane test-gateway test-agent test-policy test-gateway-e2e test-live-conversation clean clean-data run-api run-bot dev-setup manual-check-api manual-check-e2e manual-check-delegate check-stale-paths check-control-boundary check-settings-policy check-hygiene check-policy smoke format lint check

# Default target
help:
	@echo "SKYNET Development Commands"
	@echo "============================"
	@echo ""
	@echo "Setup:"
	@echo "  make install      - Install control-plane and shared dependencies"
	@echo "  make install-agent - Install worker-agent dependencies"
	@echo "  make install-all  - Install both shared and worker-agent dependencies"
	@echo "  make dev-setup    - Complete development setup"
	@echo ""
	@echo "Testing:"
	@echo "  make test         - Run curated control-plane and policy tests"
	@echo "  make test-all     - Run the curated full repo matrix"
	@echo "  make test-unit    - Alias of control-plane tests"
	@echo "  make test-control-plane - Run curated root tests"
	@echo "  make test-gateway - Run gateway tests"
	@echo "  make test-agent   - Run agent tests"
	@echo "  make test-policy  - Run repo policy tests"
	@echo "  make test-gateway-e2e - Run deterministic gateway conversation E2E tests"
	@echo "  make test-live-conversation - Run manual live conversation E2E test"
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
	@echo "  make check-settings-policy - Enforce shared settings source-of-truth policy"
	@echo "  make check-hygiene - Enforce repo hygiene and curated test topology"
	@echo "  make check-policy - Enforce engineering policy docs/evidence rules"
	@echo "  make smoke        - Quick repo health checks"
	@echo "  make check        - Run all checks"

install:
	pip install -r requirements.txt

install-agent:
	pip install -r openclaw-agent/requirements.txt

install-all: install install-agent

dev-setup: install-all
	@echo "Setting up development environment..."
	@python -c "from pathlib import Path; src=Path('.env.example'); dst=Path('.env'); (dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8'), print('Created .env file - please configure it')) if (src.exists() and not dst.exists()) else print('.env already exists or .env.example missing')"
	@echo "Development setup complete!"

test:
	@echo "Running curated control-plane and policy tests..."
	$(MAKE) test-control-plane
	$(MAKE) test-policy

test-all:
	@echo "Running curated full repo matrix..."
	$(MAKE) test-control-plane
	$(MAKE) test-gateway
	$(MAKE) test-agent
	$(MAKE) test-policy

test-unit:
	@echo "Running control-plane unit tests..."
	$(MAKE) test-control-plane

test-control-plane:
	@echo "Running curated root control-plane and repo-policy tests..."
	python -m pytest tests/test_api_lifespan.py tests/test_api_provider_config.py tests/test_api_control_plane.py tests/test_job_locking.py tests/test_task_queue_control_plane.py tests/test_worker_registry.py tests/test_ci_engineering_policy.py tests/test_prompt_references.py -q

test-gateway:
	@echo "Running gateway tests..."
	python -m pytest openclaw-gateway/tests -q

test-agent:
	@echo "Running agent tests..."
	python -m pytest openclaw-agent/tests -q

test-policy:
	@echo "Running policy and hygiene guards..."
	python scripts/ci/check_stale_paths.py
	python scripts/ci/check_control_plane_boundary.py
	python scripts/ci/check_settings_policy.py
	python scripts/ci/check_repo_hygiene.py
	python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD

test-gateway-e2e:
	@echo "Running deterministic gateway conversation E2E tests..."
	python -m pytest openclaw-gateway/tests/test_conversation_e2e_repo_push.py -q

test-live-conversation:
	@echo "Running manual live conversation E2E test..."
	python -m pytest openclaw-gateway/tests/test_e2e_conversation_live.py -m live -q

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

check-settings-policy:
	@echo "Checking shared settings policy..."
	python scripts/ci/check_settings_policy.py

check-hygiene:
	@echo "Checking repo hygiene..."
	python scripts/ci/check_repo_hygiene.py

check-policy:
	@echo "Checking engineering policy compliance..."
	python scripts/ci/check_engineering_policy.py --base-ref HEAD~1 --head-ref HEAD

smoke: check-stale-paths check-control-boundary check-settings-policy check-hygiene check-policy
	@echo "Running smoke checks..."
	python scripts/dev/smoke.py

clean:
	@echo "Cleaning cache and temporary files..."
	@python -c "from pathlib import Path; import shutil; root=Path('.'); scratch_dirs=[root / '.pytest_cache', root / '.pytest-qwen-probe', root / '.pytest-tmp', root / '.tmp', root / 'openclaw-gateway/tests/.artifacts', root / 'openclaw-agent/MyProjectsskynetlogs']; scratch_dirs.extend(root.glob('tmp-probe-dir*')); [shutil.rmtree(p, ignore_errors=True) for p in root.rglob('__pycache__') if p.is_dir()]; [p.unlink(missing_ok=True) for p in root.rglob('*.pyc') if p.is_file()]; [shutil.rmtree(p, ignore_errors=True) for p in scratch_dirs]; print('Clean complete!')"

clean-data:
	@echo "Cleaning test data..."
	@python -c "from pathlib import Path; import shutil; [shutil.rmtree(p, ignore_errors=True) for p in Path('data').glob('test_*')] if Path('data').exists() else None; print('Test data cleaned!')"

format:
	@echo "Formatting code with black..."
	black skynet/ openclaw-gateway/ openclaw-agent/ tests/ scripts/

lint:
	@echo "Running linters..."
	flake8 skynet/ openclaw-gateway/ openclaw-agent/ tests/ scripts/ --max-line-length=100

check: clean test lint
	@echo "All checks passed!"
