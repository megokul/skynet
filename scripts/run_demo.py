"""Run SKYNET demo from project root."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.main import demo

if __name__ == "__main__":
    asyncio.run(demo())
