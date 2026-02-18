"""
SKYNET control-plane primitives.

These modules intentionally avoid direct workload execution and only
manage gateway/worker orchestration metadata and gateway API delegation.
"""

from .gateway_client import GatewayClient
from .registry import ControlPlaneRegistry

__all__ = ["ControlPlaneRegistry", "GatewayClient"]
