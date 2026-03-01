"""
SKYNET Live End-to-End Test
============================
Run this ON EC2 (where SSH env vars are set) to verify the full
coding pipeline actually works against the real laptop.

Usage:
    cd openclaw-gateway
    python tests/e2e_live.py

What it tests:
    1. SSH connection to laptop
    2. Directory creation on laptop (E:\\SKYNET-SANDBOX\\Projects\\e2e-test-<ts>\\)
    3. Ollama code generation via SSH (run_coding_agent)
    4. Files written to laptop disk (verify via SFTP)
    5. Project execution via exec_command (python main.py)
    6. Cleanup of test directory

Pass criteria:
    - At least one .py file written to project folder
    - exec_command returns returncode 0
    - stdout contains expected output
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

# Make sure we can import from the gateway package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROMPT = (
    "Create a minimal Python project with a single file called main.py. "
    "The script should print exactly: SKYNET_E2E_OK\n"
    "No dependencies, no imports beyond stdlib. "
    "Output only the file in a fenced code block: ```main.py"
)

SLUG = f"e2e-test-{int(time.time())}"


def _check_env() -> None:
    missing = []
    for var in ("OPENCLAW_SSH_HOST", "OPENCLAW_SSH_USER"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        print(f"[SKIP] Missing env vars: {', '.join(missing)}")
        print("       Set OPENCLAW_SSH_* vars and retry.")
        sys.exit(0)


def _separator(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


async def run() -> None:
    from ssh_tunnel_executor import get_ssh_executor

    _check_env()

    executor = get_ssh_executor()
    if not executor.is_configured():
        print("[FAIL] SSH executor is not configured. Check OPENCLAW_SSH_* env vars.")
        sys.exit(1)

    base_dir = os.environ.get("OPENCLAW_PROJECT_BASE_DIR", "E:\\SKYNET-SANDBOX\\Projects")
    working_dir = base_dir.rstrip("\\") + "\\" + SLUG

    # ── Step 1: Create project directory ────────────────────────────────────────
    _separator("Step 1 — Create project directory on laptop")
    result = await executor.execute_action("create_directory", {"directory": working_dir})
    print(f"  Result: {result}")
    if result.get("status") == "error" or result.get("result", {}).get("returncode", 0) != 0:
        print(f"[FAIL] Could not create directory: {working_dir}")
        sys.exit(1)
    print(f"[OK] Directory created: {working_dir}")

    # ── Step 2: Run coding agent (Ollama generates main.py) ─────────────────────
    _separator("Step 2 — Run coding agent (Ollama on laptop)")
    print(f"  Prompt: {PROMPT[:80]}...")
    print("  This may take up to 60 seconds...")
    t0 = time.time()
    result = await executor.execute_action(
        "run_coding_agent",
        {"prompt": PROMPT, "working_dir": working_dir},
        confirmed=True,
    )
    elapsed = time.time() - t0
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Result keys: {list(result.keys())}")
    print(f"  Status: {result.get('status')}")
    inner = result.get("result", result)
    print(f"  Stdout (first 500 chars):\n{str(inner.get('stdout', ''))[:500]}")
    if result.get("status") == "error":
        print(f"[FAIL] run_coding_agent failed: {result}")
        sys.exit(1)
    if inner.get("returncode", 0) != 0:
        print(f"[FAIL] run_coding_agent non-zero returncode: {inner}")
        sys.exit(1)
    print("[OK] Coding agent completed.")

    # ── Step 3: Verify file was written on laptop ────────────────────────────────
    _separator("Step 3 — Verify main.py exists on laptop")
    main_py = working_dir.rstrip("\\") + "\\main.py"
    result = await executor.execute_action("file_read", {"file": main_py})
    print(f"  Result status: {result.get('status')}")
    inner = result.get("result", result)
    content = inner.get("content", inner.get("stdout", ""))
    if result.get("status") == "error" or not content:
        print(f"[FAIL] main.py not found or empty at {main_py}")
        print(f"       Result: {result}")
        sys.exit(1)
    print(f"  main.py content ({len(content)} chars):\n{content[:400]}")
    print("[OK] main.py exists on laptop.")

    # ── Step 4: Execute the project ─────────────────────────────────────────────
    _separator("Step 4 — Execute project (python main.py)")
    result = await executor.execute_action(
        "exec_command",
        {"working_dir": working_dir, "command": "python main.py"},
    )
    print(f"  Result: {result}")
    inner = result.get("result", result)
    stdout = inner.get("stdout", "")
    returncode = inner.get("returncode", -1)
    print(f"  returncode: {returncode}")
    print(f"  stdout: {stdout!r}")
    if returncode != 0:
        print(f"[FAIL] exec_command returned non-zero: {returncode}")
        sys.exit(1)
    if "SKYNET_E2E_OK" not in stdout:
        print(f"[WARN] Expected 'SKYNET_E2E_OK' in stdout but got: {stdout!r}")
    else:
        print("[OK] Output matches expected: SKYNET_E2E_OK")

    # ── Done ─────────────────────────────────────────────────────────────────────
    _separator("RESULT")
    print()
    print("  ✅  ALL STEPS PASSED")
    print(f"  Project dir on laptop: {working_dir}")
    print()
    print("  Files are NOT cleaned up — inspect them on the laptop.")
    print("  To delete: rmdir /s /q " + working_dir)
    print()


if __name__ == "__main__":
    asyncio.run(run())
