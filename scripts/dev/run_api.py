"""
SKYNET FastAPI Service - Startup Script

Loads environment variables from .env and starts uvicorn server.

Usage:
    python scripts/dev/run_api.py
"""

import os
from pathlib import Path

# Load .env file
from dotenv import load_dotenv

repo_root = Path(__file__).resolve().parents[2]
env_path = repo_root / ".env"
load_dotenv(env_path)

# Verify API key is loaded
if not os.getenv("GOOGLE_AI_API_KEY"):
    print("WARNING: GOOGLE_AI_API_KEY not found in .env")
else:
    print(f"GOOGLE_AI_API_KEY loaded: {os.getenv('GOOGLE_AI_API_KEY')[:20]}...")

# Start uvicorn
if __name__ == "__main__":
    import uvicorn

    print("\nStarting SKYNET FastAPI service...")
    uvicorn.run(
        "skynet.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
