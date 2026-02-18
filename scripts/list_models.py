"""List available Gemini models."""
import asyncio
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

async def main():
    api_key = os.getenv("GOOGLE_AI_API_KEY")
    client = genai.Client(api_key=api_key)

    print("Available Gemini models:")
    print("=" * 60)

    models = await client.aio.models.list()
    for model in models:
        if hasattr(model, 'name'):
            print(f"  - {model.name}")
            if hasattr(model, 'supported_generation_methods'):
                print(f"    Methods: {model.supported_generation_methods}")

asyncio.run(main())
