from __future__ import annotations

import logging
import aiohttp

from core.prompt_library import commander_prompt_block, load_prompt
from core.roles.base import Role, RoleContext, RoleOutput
from core.trace import trace_flow
from core.tracing import trace

logger = logging.getLogger("skynet.core.roles.weather")


class WeatherSpecialistRole(Role):
    name = "weather_specialist"
    _guidance = commander_prompt_block()
    _location_extract_instruction = load_prompt("core/roles/weather_location_extract_instruction.md")

    @trace(role="weather_specialist", step_name="weather_specialist_handle")
    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        trace_flow(
            "role.weather.handle.start",
            conversation_id=context.conversation.id,
            text=user_text,
        )
        location = await self._extract_location(context, user_text)
        if not location:
            return RoleOutput(command="continue", response="Which location should I check weather for?")

        try:
            summary = await self._fetch_weather(location)
            trace_flow(
                "role.weather.handle.complete",
                conversation_id=context.conversation.id,
                location=location,
                summary=summary,
            )
            return RoleOutput(command="complete", response=summary, result={"location": location})
        except Exception as exc:
            logger.exception("Weather fetch failed location=%s", location)
            trace_flow(
                "role.weather.handle.error",
                conversation_id=context.conversation.id,
                location=location,
                error=str(exc),
            )
            return RoleOutput(
                command="complete",
                response=f"I could not fetch weather for '{location}' right now: {exc}",
            )

    @trace(
        role="weather_specialist",
        prompt="prompts/core/roles/weather_location_extract_instruction.md",
        step_name="extract_weather_location",
    )
    async def _extract_location(self, context: RoleContext, user_text: str) -> str:
        extractor = context.intent_extractor
        if extractor is None:
            return (user_text or "").strip()

        data = await extractor.extract_payload(
            user_text,
            {"location": "", "confidence": 0.0},
            instruction=self._location_extract_instruction,
        )
        location = str(data.get("location") or "").strip()
        confidence = float(data.get("confidence") or 0.0)
        if location and confidence >= 0.45:
            return location
        return (user_text or "").strip()

    @trace(role="weather_specialist", step_name="fetch_weather")
    async def _fetch_weather(self, location: str) -> str:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
            ) as resp:
                geo = await resp.json()

            results = geo.get("results") or []
            if not results:
                return f"I could not find weather location '{location}'."

            item = results[0]
            lat = item.get("latitude")
            lon = item.get("longitude")
            pretty = item.get("name") or location
            country = item.get("country")
            if country:
                pretty = f"{pretty}, {country}"

            async with session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,wind_speed_10m,weather_code",
                    "timezone": "auto",
                },
            ) as resp:
                forecast = await resp.json()

        current = forecast.get("current") or {}
        temp = current.get("temperature_2m")
        wind = current.get("wind_speed_10m")
        code = current.get("weather_code")
        return (
            f"Weather for {pretty}: {temp}C, wind {wind} km/h, code {code}. "
            "Igris is back in command."
        )
