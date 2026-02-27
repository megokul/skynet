"""
Weather specialist role.

The role extracts a location from natural language, fetches current weather
from Open-Meteo APIs, and returns a concise summary.
"""

from __future__ import annotations

import logging
import aiohttp

from core.dev_trace import (
    DevTracePhase,
    trace_control_flow,
    trace_decision,
    trace_output,
    trace_role_enter,
)
from core.prompt_library import load_prompt
from core.roles.base import Role, RoleContext, RoleOutput

logger = logging.getLogger("skynet.core.roles.weather")


class WeatherSpecialistRole(Role):
    """
    WeatherSpecialistRole.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `WeatherSpecialistRole`.
    """

    name = "weather_specialist"
    _location_extract_instruction = load_prompt("core/roles/weather_location_extract_instruction.md")

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Weather role lifecycle:
        1. Extract location from natural text.
        2. Query geocoding and forecast endpoints.
        3. Return concise result and complete role turn.
        """
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        trace_role_enter(DevTracePhase.SPECIALIST, self.name)
        location = await self._extract_location(context, user_text)
        if not location:
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "weather requires location",
                    "selected_action": "continue",
                    "reasoning": "location extraction returned empty",
                },
            )
            return RoleOutput(command="continue", response="Which location should I check weather for?")

        try:
            summary = await self._fetch_weather(location)
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "fetch weather and complete",
                    "selected_action": "complete",
                    "location": location,
                    "reasoning": "location extracted successfully",
                },
            )
            trace_output(DevTracePhase.SPECIALIST, key="weather_summary", value=summary)
            return RoleOutput(command="complete", response=summary, result={"location": location})
        except Exception as exc:
            logger.exception("Weather fetch failed location=%s", location)
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "weather fetch error fallback",
                    "selected_action": "complete",
                    "location": location,
                    "reasoning": str(exc),
                },
            )
            return RoleOutput(
                command="complete",
                response=f"I could not fetch weather for '{location}' right now: {exc}",
            )

    async def _extract_location(self, context: RoleContext, user_text: str) -> str:
        """
        Use structured payload extraction for location.

        If extraction confidence is low, fall back to raw user text so weather
        lookup still has a chance to succeed.
        """
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
            trace_output(DevTracePhase.SPECIALIST, key="weather_location", value=location)
            return location
        return (user_text or "").strip()

    async def _fetch_weather(self, location: str) -> str:
        """
        Fetch weather via Open-Meteo APIs.

        Two-step call chain:
        - Geocoding API resolves location -> lat/lon.
        - Forecast API resolves lat/lon -> current weather snapshot.
        """
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
