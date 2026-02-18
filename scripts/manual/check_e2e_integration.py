"""
E2E integration check - OpenClaw Gateway <-> SKYNET control plane.

Checks:
1. SKYNET health endpoint
2. OpenClaw gateway status endpoint
3. SKYNET route-task call (which forwards to OpenClaw gateway API)
"""

import asyncio
import os
import sys

import httpx

OPENCLAW_API = "http://localhost:8766"
SKYNET_API = "http://localhost:8000"
SKYNET_API_KEY = os.getenv("SKYNET_API_KEY", "").strip()


def _skynet_headers() -> dict[str, str]:
    headers = {}
    if SKYNET_API_KEY:
        headers["X-API-Key"] = SKYNET_API_KEY
    return headers


async def test_openclaw_skynet_integration() -> bool:
    print("=" * 70)
    print("E2E Integration Test: OpenClaw <-> SKYNET")
    print("=" * 70)

    async with httpx.AsyncClient(timeout=30.0) as client:
        print("\n1. Verifying services...")
        try:
            skynet_health = await client.get(f"{SKYNET_API}/v1/health")
            if skynet_health.status_code != 200:
                print(f"   [FAIL] SKYNET health returned {skynet_health.status_code}")
                return False
            print(f"   [OK] SKYNET API is running: {skynet_health.json().get('status')}")
        except Exception as exc:
            print(f"   [FAIL] Cannot reach SKYNET API: {exc}")
            return False

        try:
            gateway_status = await client.get(f"{OPENCLAW_API}/status")
            if gateway_status.status_code != 200:
                print(f"   [FAIL] OpenClaw Gateway returned {gateway_status.status_code}")
                return False
            print("   [OK] OpenClaw Gateway is running")
        except Exception as exc:
            print(f"   [FAIL] Cannot reach OpenClaw Gateway: {exc}")
            return False

        print("\n2. Registering OpenClaw gateway in SKYNET...")
        register_payload = {
            "gateway_id": "e2e-gateway",
            "host": OPENCLAW_API,
            "capabilities": ["execute_task", "get_gateway_status", "list_sessions"],
            "status": "online",
            "metadata": {"source": "manual-e2e"},
        }
        register_resp = await client.post(
            f"{SKYNET_API}/v1/register-gateway",
            json=register_payload,
            headers=_skynet_headers(),
        )
        if register_resp.status_code != 200:
            print(f"   [FAIL] register-gateway failed: {register_resp.status_code}")
            print(f"       Error: {register_resp.text}")
            return False
        print("   [OK] Gateway registered")

        print("\n3. Routing task through SKYNET...")
        route_payload = {
            "action": "list_directory",
            "params": {"directory": "."},
            "gateway_id": "e2e-gateway",
            "confirmed": True,
        }
        route_resp = await client.post(
            f"{SKYNET_API}/v1/route-task",
            json=route_payload,
            headers=_skynet_headers(),
        )
        if route_resp.status_code != 200:
            print(f"   [FAIL] route-task failed: {route_resp.status_code}")
            print(f"       Error: {route_resp.text}")
            return False

        route_result = route_resp.json()
        print("   [OK] route-task succeeded")
        print(f"       Task ID: {route_result.get('task_id')}")
        print(f"       Gateway: {route_result.get('gateway_id')}")
        print(f"       Status: {route_result.get('status')}")
        return True


async def main() -> int:
    success = await test_openclaw_skynet_integration()
    print("\n" + "=" * 70)
    if success:
        print("E2E Integration Test: PASSED")
    else:
        print("E2E Integration Test: FAILED")
        print(f"Check SKYNET at {SKYNET_API} and OpenClaw at {OPENCLAW_API}")
    print("=" * 70)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
