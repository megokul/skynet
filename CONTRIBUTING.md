# Contributing to SKYNET

Welcome! This guide will help you get started with SKYNET development.

## 🚀 Quick Start for Developers

### 1. Setup Development Environment

```bash
# Clone the repository
git clone <your-repo-url>
cd skynet

# Set up development environment
make dev-setup

# Or manually:
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your configuration
```

### 2. Project Structure

```
skynet/
├── skynet/          # Main package - all production code here
├── tests/           # All test files
├── scripts/         # Utility and runner scripts
├── docs/            # Documentation
└── Makefile         # Common development commands
```

### 3. Running Tests

```bash
# Quick test (recommended before commit)
make test

# All tests
make test-all

# Specific test categories
make test-unit       # Unit tests only
make test-e2e        # End-to-end tests
```

## 📝 Development Workflow

### Making Changes

1. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Write code**
   - Follow existing code patterns
   - See [docs/guides/DEVELOPMENT.md](docs/guides/DEVELOPMENT.md) for conventions

3. **Write tests**
   - Every new feature needs tests
   - Place tests in `tests/` directory
   - Follow naming: `test_<component>.py`

4. **Run tests**
   ```bash
   make test-all
   ```

5. **Clean up**
   ```bash
   make clean
   ```

6. **Commit**
   ```bash
   git add .
   git commit -m "feat: your feature description"
   ```

### Code Style

- **Python**: Follow PEP 8
- **Type Hints**: Required for all functions
- **Docstrings**: Google style
- **Line Length**: 100 characters max

```python
def example_function(param: str, option: int = 0) -> dict[str, Any]:
    """
    Brief description of function.

    Args:
        param: Description of param
        option: Description of option (default: 0)

    Returns:
        Dictionary with results
    """
    return {"result": "value"}
```

## 🧪 Testing Guidelines

### Test Structure

```python
"""
Test ComponentName — Brief Description

Detailed explanation of what this test file covers.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.component import Component


def test_component_initialization():
    """Test component initialization."""
    print("\n[TEST 1] Component initialization")

    component = Component()
    assert component is not None
    print("  [PASS] Component initialized")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Component Tests")
    print("=" * 60)

    try:
        test_component_initialization()
        # More tests...

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        return True
    except AssertionError as e:
        print(f"\n[FAILED] Test failed: {e}")
        return False


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
```

### Test Categories

Mark tests with appropriate markers:

```python
import pytest

@pytest.mark.unit
def test_unit_example():
    """Unit test example."""
    pass

@pytest.mark.integration
def test_integration_example():
    """Integration test example."""
    pass

@pytest.mark.e2e
def test_e2e_example():
    """End-to-end test example."""
    pass

@pytest.mark.slow
def test_slow_example():
    """Slow-running test."""
    pass
```

## 📚 Documentation

### When Adding New Features

Update these 5 files (mandatory):
1. **CLAUDE.md** - Project status and component list
2. **TODO.md** - Task completion status
3. **docs/SESSION_NOTES.md** - Development history
4. **docs/guides/AGENT_GUIDE.md** - If workflow changed
5. **docs/guides/DEVELOPMENT.md** - If patterns changed

See [POLICY.md](POLICY.md) for details.

### Documentation Style

- Use clear, concise language
- Include code examples
- Add links to related documentation
- Update README.md if user-facing changes

## 🔧 Common Development Tasks

### Run Specific Tests

```bash
# Single test file
python tests/test_planner.py

# With pytest
python -m pytest tests/test_planner.py -v

# Specific test function
python -m pytest tests/test_planner.py::test_generate_plan -v
```

### Debug Tests

```python
# Add breakpoint in test
def test_example():
    component = Component()
    breakpoint()  # Debugger will stop here
    assert component.status == "ok"
```

### Add New Provider

1. Create provider file: `skynet/chathan/providers/my_provider.py`
2. Implement interface: `execute()`, `health_check()`, `cancel()`
3. Add to worker: `skynet/queue/worker.py`
4. Create tests: `tests/test_my_provider.py`
5. Update documentation

### Add New Test

```bash
# Create test file
touch tests/test_new_component.py

# Use existing test as template
cp tests/test_planner.py tests/test_new_component.py

# Edit and implement tests
# Run to verify
python tests/test_new_component.py
```

## 🐛 Debugging

### Common Issues

**Import errors:**
```python
# Make sure this is at the top of test files:
sys.path.insert(0, str(Path(__file__).parent.parent))
```

**Environment issues:**
```bash
# Verify environment
python -c "import skynet; print(skynet.__file__)"

# Check dependencies
pip list | grep -E "google-genai|celery|aiohttp"
```

**Test failures:**
```bash
# Run with verbose output
python -m pytest tests/test_failing.py -vv

# Run with print statements
python -m pytest tests/test_failing.py -s
```

## 🎯 Best Practices

### Code Quality

- ✅ Write tests before or with code
- ✅ Use type hints everywhere
- ✅ Keep functions small and focused
- ✅ Avoid global state
- ✅ Handle errors explicitly

### Git Commits

Use conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `test:` Tests
- `refactor:` Code refactoring
- `chore:` Maintenance

Example:
```bash
git commit -m "feat: add SSH provider timeout configuration"
git commit -m "fix: resolve race condition in job locking"
git commit -m "docs: update provider setup guide"
```

### Pull Requests

1. Update documentation
2. Add/update tests
3. Run `make check` before submitting
4. Write clear PR description
5. Link related issues

## 🆘 Getting Help

### Resources

- **README.md** - Project overview and quick start
- **CLAUDE.md** - Complete project context
- **QUICK_START.md** - 30-minute tutorial
- **docs/guides/** - Detailed implementation guides

### Questions?

- Check existing documentation first
- Review similar components for patterns
- Look at tests for usage examples

## 📊 Project Status

Current phase: **100% Complete**

All 7 phases implemented:
- ✅ Phase 1: Core (Planner, Dispatcher, Orchestrator)
- ✅ Phase 2: Ledger (State, Registry, Locking)
- ✅ Phase 3: Archive (Artifacts, Logs)
- ✅ Phase 4: Sentinel (Monitoring, Alerts)
- ✅ Phase 5: Providers (5 execution backends)
- ✅ Phase 6: Integration (Telegram, Worker)
- ✅ Phase 7: Testing (150+ scenarios)

See [TODO.md](TODO.md) for detailed status.

---

**Thank you for contributing to SKYNET!**

For questions or suggestions, refer to the documentation or review existing code patterns.
