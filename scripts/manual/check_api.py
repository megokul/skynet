"""Manual checks for SKYNET control-plane API."""

import asyncio
import json
import os
import sys
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = "http://localhost:8000"
API_KEY = os.getenv("SKYNET_API_KEY", "").strip()


def _headers() -> dict[str, str]:
    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


async def test_health() -> bool:
    print("\n=== Testing /v1/health ===")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_BASE}/v1/health")
        print(f"Status: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        return response.status_code == 200


async def test_register_gateway() -> bool:
    print("\n=== Testing /v1/register-gateway ===")
    payload = {
        "gateway_id": "manual-gw-1",
        "host": "http://127.0.0.1:8766",
        "capabilities": ["execute_task", "get_gateway_status", "list_sessions"],
        "status": "online",
        "metadata": {"source": "manual-check"},
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/register-gateway",
            json=payload,
            headers=_headers(),
        )
        print(f"Status: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        return response.status_code == 200


async def test_register_worker() -> bool:
    print("\n=== Testing /v1/register-worker ===")
    payload = {
        "worker_id": "manual-worker-1",
        "gateway_id": "manual-gw-1",
        "capabilities": ["shell", "filesystem"],
        "status": "online",
        "capacity": {"cpu": 4, "memory_mb": 8192},
        "metadata": {"source": "manual-check"},
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/v1/register-worker",
            json=payload,
            headers=_headers(),
        )
        print(f"Status: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        return response.status_code == 200


async def test_route_task() -> bool:
    print("\n=== Testing /v1/route-task ===")
    payload = {
        "task_id": str(uuid4()),
        "action": "git_status",
        "params": {"working_dir": "."},
        "gateway_id": "manual-gw-1",
        "confirmed": True,
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            f"{API_BASE}/v1/route-task",
            json=payload,
            headers=_headers(),
        )
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            print(json.dumps(response.json(), indent=2))
        else:
            print(response.text)
        return response.status_code == 200


async def test_system_state() -> bool:
    print("\n=== Testing /v1/system-state ===")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_BASE}/v1/system-state", headers=_headers())
        print(f"Status: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        return response.status_code == 200


async def main() -> int:
    print("=" * 70)
    print("SKYNET Control Plane API Manual Checks")
    print("=" * 70)

    results = [
        ("Health", await test_health()),
        ("Register Gateway", await test_register_gateway()),
        ("Register Worker", await test_register_worker()),
        ("System State", await test_system_state()),
        ("Route Task", await test_route_task()),
    ]

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    for name, passed in results:
        print(f"{'[PASS]' if passed else '[FAIL]'} - {name}")

    all_passed = all(passed for _, passed in results)
    print("\n" + ("All checks passed!" if all_passed else "Some checks failed"))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
