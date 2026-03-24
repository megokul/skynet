"""
SKYNET — Multi-Provider AI Router

Automatically selects the best available free-tier AI provider for
each request.  Tracks per-provider usage, rotates on quota exhaustion,
and retries with fallback providers on errors.

Priority order (configurable):
  1. Gemini Flash  — biggest free tier (1M tokens/day)
  2. Groq           — fast, 14.4K req/day free
  3. OpenRouter     — various free models
  4. DeepSeek       — near-free
  5. OpenAI         — if credits available
  6. Claude         — if credits available
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from .providers.base import BaseProvider, ProviderResponse
from db import store

logger = logging.getLogger("skynet.ai.router")

OPENROUTER_FREE_MODEL_PRIORITY: list[str] = [
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "qwen/qwen3-vl-235b-a22b-thinking",
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder:free",
    "google/gemma-3n-e2b-it:free",
    "deepseek/deepseek-r1-0528:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]


class ProviderRouter:
    """Routes AI requests to the best available free-tier provider."""

    def __init__(
        self,
        providers: list[BaseProvider],
        db: aiosqlite.Connection,
        provider_priority: list[str] | None = None,
    ):

        self.providers = providers
        self.db = db
        self._error_counts: dict[str, int] = {}
        self._provider_priority = provider_priority or [
            "claude",
            "gemini",
            "openai",
            "deepseek",
            "openrouter",
            "groq",
            "ollama",
        ]
        self._provider_rank = {
            name: idx for idx, name in enumerate(self._provider_priority)
        }

    async def restore_usage(self) -> None:
        """Load today's usage counters from the database."""
        for provider in self.providers:
            usage = await store.get_provider_usage(self.db, provider.name)
            if usage:
                provider.load_usage_from_db(usage["requests_used"], usage["date"])
                logger.info(
                    "Restored %s usage: %d requests today",
                    provider.name, usage["requests_used"],
                )

    # Task-type → preferred provider names.
    TASK_PROVIDER_PREFERENCES: dict[str, list[str]] = {
        "scaffold": ["dashscope", "ollama"],
        "crud": ["dashscope", "ollama"],
        "boilerplate": ["dashscope", "ollama"],
        "fast_patch": ["groq", "ollama"],
        "unit_test": ["groq", "ollama"],
        "coding": ["dashscope", "gemini", "groq", "claude"],
        "planning": ["dashscope", "gemini", "ollama"],
        "readme_polish": ["dashscope", "ollama", "gemini"],
        "hard_debug": ["deepseek", "claude"],
        "complex_refactor": ["dashscope", "ollama", "claude"],
        "general": [],  # default priority order
        # v3: Agent-role routing
        "agent_architect": ["dashscope", "ollama", "gemini", "claude"],
        "agent_backend": ["dashscope", "groq", "ollama"],
        "agent_frontend": ["dashscope", "groq", "ollama"],
        "agent_api": ["dashscope", "groq", "ollama"],
        "agent_testing": ["groq", "ollama"],
        "agent_debug": ["deepseek", "claude"],
        "agent_devops": ["dashscope", "groq", "ollama"],
        "agent_research": ["dashscope", "ollama", "gemini"],
        "agent_optimization": ["deepseek", "claude"],
        "agent_deployment": ["dashscope", "groq", "ollama"],
        "agent_monitoring": ["dashscope", "groq", "ollama"],
    }

    def _ranked_providers(
        self,
        require_tools: bool = False,
        task_type: str = "general",
        preferred_provider: str | None = None,
        preferred_provider_only: bool = False,
        allowed_providers: set[str] | None = None,
    ) -> list[BaseProvider]:
        """
        Return providers sorted by task-aware priority, filtered by
        availability, cooldown, and quota.
        """
        preferences = self.TASK_PROVIDER_PREFERENCES.get(task_type, [])
        scored: list[tuple[BaseProvider, int]] = []

        normalized_allowlist = allowed_providers
        if allowed_providers is not None:
            normalized_allowlist = set(allowed_providers)
            # Compatibility alias for Anthropic provider naming.
            if "anthropic" in normalized_allowlist:
                normalized_allowlist.add("claude")
            if "claude" in normalized_allowlist:
                normalized_allowlist.add("anthropic")

        for p in self.providers:
            if normalized_allowlist is not None and p.name not in normalized_allowlist:
                continue
            if preferred_provider_only and preferred_provider and p.name != preferred_provider:
                continue
            if require_tools and not p.supports_tool_use:
                continue
            if not p.has_quota():
                continue
            if p.is_in_cooldown():
                continue

            # Build a priority score (lower = better).
            if preferred_provider and p.name == preferred_provider:
                score = -1  # Highest priority.
            elif p.name in preferences:
                score = preferences.index(p.name)
            else:
                rank = self._provider_rank.get(p.name, 999)
                score = 50 + (rank * 10) + p.cost_rank
                if p.is_deprioritized():
                    score += 1_000

            scored.append((p, score))

        scored.sort(key=lambda x: x[1])
        return [p for p, _ in scored]

    def available_provider_names(
        self,
        *,
        require_tools: bool = False,
        task_type: str = "general",
        preferred_provider: str | None = None,
        preferred_provider_only: bool = False,
        allowed_providers: list[str] | set[str] | None = None,
    ) -> list[str]:
        """
        Return currently available provider names for a routing shape.

        This is a read-only introspection helper used by orchestration layers
        to choose resilient allowlists before issuing a chat request.
        """
        if isinstance(allowed_providers, set):
            allowlist = allowed_providers
        elif allowed_providers is None:
            allowlist = None
        else:
            allowlist = set(allowed_providers)

        candidates = self._ranked_providers(
            require_tools=require_tools,
            task_type=task_type,
            preferred_provider=preferred_provider,
            preferred_provider_only=preferred_provider_only,
            allowed_providers=allowlist,
        )
        return [provider.name for provider in candidates]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        require_tools: bool = False,
        task_type: str = "general",
        preferred_provider: str | None = None,
        preferred_provider_only: bool = False,
        allowed_providers: list[str] | None = None,
    ) -> ProviderResponse:
        """
        Send a chat request to the best available provider.

        Selects provider based on task_type routing, quota availability,
        cooldown state, and error history. Falls back through all candidates.
        """
        candidates = self._ranked_providers(
            require_tools=require_tools or bool(tools),
            task_type=task_type,
            preferred_provider=preferred_provider,
            preferred_provider_only=preferred_provider_only,
            allowed_providers=set(allowed_providers) if allowed_providers else None,
        )
        if not candidates:
            raise RuntimeError(
                "No AI providers available — all quotas exhausted or in cooldown. "
                "Quotas reset daily; try again later or add more API keys."
            )

        last_error: Exception | None = None
        for provider in candidates:
            try:
                logger.info(
                    "Trying provider: %s (%s) for task_type=%s",
                    provider.name, provider.model_name, task_type,
                )
                response = await provider.chat(
                    messages, tools=tools, system=system, max_tokens=max_tokens,
                )
                # Success — record usage and clear errors.
                provider.record_usage(response.input_tokens + response.output_tokens)
                await store.record_provider_usage(
                    self.db,
                    provider_name=provider.name,
                    requests=1,
                    tokens=response.input_tokens + response.output_tokens,
                )
                self._error_counts[provider.name] = 0
                return response

            except Exception as exc:
                error_str = str(exc)
                logger.warning(
                    "Provider %s failed: %s — trying next", provider.name, error_str,
                )
                provider.record_error()
                self._error_counts[provider.name] = self._error_counts.get(provider.name, 0) + 1

                # Detect rate limiting (429) and enter cooldown after 2 hits.
                if "429" in error_str or "rate" in error_str.lower():
                    rate_key = f"_429_{provider.name}"
                    count = self._error_counts.get(rate_key, 0) + 1
                    self._error_counts[rate_key] = count
                    if count >= 2:
                        provider.enter_cooldown(300)
                        logger.warning(
                            "Provider %s entering 5-min cooldown after 2x rate limit",
                            provider.name,
                        )

                await store.record_provider_usage(
                    self.db, provider_name=provider.name, requests=0, tokens=0, error=True,
                )
                last_error = exc
                continue

        raise RuntimeError(
            f"All AI providers failed. Last error: {last_error}"
        )

    async def get_quota_summary(self) -> list[dict[str, Any]]:
        """Return quota info for all providers (for /quota command)."""
        summary = []
        for p in self.providers:
            q = p.remaining_quota()
            summary.append({
                "provider": p.name,
                "model": p.model_name,
                "daily_used": q.daily_used,
                "daily_limit": q.daily_limit,
                "rpm_used": q.rpm_used,
                "rpm_limit": q.rpm_limit,
                "available": p.has_quota(),
            })
        return summary


def _is_truthy(value: Any) -> bool:

    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_provider_priority(value: str | None) -> list[str]:

    if not value:
        return []
    parsed = [
        item.strip().lower()
        for item in value.replace(";", ",").split(",")
        if item.strip()
    ]
    deduped: list[str] = []
    for item in parsed:
        if item not in deduped:
            deduped.append(item)
    return deduped


def build_providers(config: dict[str, str]) -> list[BaseProvider]:
    """
    Instantiate all available providers from config/env vars.

    Only providers with valid API keys are included.
    If GEMINI_ONLY_MODE is enabled, only Gemini is registered.
    """
    from .providers.ollama_proxy import OllamaProxyProvider
    try:
        from .providers.gemini import GeminiProvider
    except ModuleNotFoundError as exc:
        GeminiProvider = None  # type: ignore[assignment]
        logger.warning("Gemini provider unavailable: %s", exc)
    try:
        from .providers.groq import GroqProvider
    except ModuleNotFoundError as exc:
        GroqProvider = None  # type: ignore[assignment]
        logger.warning("Groq provider unavailable: %s", exc)
    try:
        from .providers.openai_compat import OpenAICompatProvider
    except ModuleNotFoundError as exc:
        OpenAICompatProvider = None  # type: ignore[assignment]
        logger.warning("OpenAI-compatible providers unavailable: %s", exc)

    providers: list[BaseProvider] = []
    gemini_only_mode = _is_truthy(config.get("GEMINI_ONLY_MODE", "0"))

    # Strict Gemini-only runtime (no Ollama/fallback providers).
    if gemini_only_mode:
        if config.get("GOOGLE_AI_API_KEY"):
            if GeminiProvider is None:
                logger.error(
                    "GEMINI_ONLY_MODE is enabled and GOOGLE_AI_API_KEY is set, "
                    "but Gemini SDK is unavailable."
                )
            else:
                gemini_model = config.get("GEMINI_MODEL", "gemini-2.0-flash")
                providers.append(GeminiProvider(config["GOOGLE_AI_API_KEY"], model=gemini_model))
                logger.info(
                    "Registered provider: Gemini Flash (model=%s, gemini_only_mode=true)",
                    gemini_model,
                )
        else:
            logger.error(
                "GEMINI_ONLY_MODE is enabled but GOOGLE_AI_API_KEY is missing."
            )
        return providers

    # 0. DashScope (Qwen cloud) — primary chat provider (smart + free tier)
    if config.get("DASHSCOPE_API_KEY") and OpenAICompatProvider is not None:
        providers.append(OpenAICompatProvider(
            api_key=config["DASHSCOPE_API_KEY"],
            model="qwen3-coder",
            model_candidates=["qwen-plus", "qwen-turbo"],
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            provider_name="dashscope",
            cost_rank_override=0,
            context_limit_override=131_072,
        ))
        logger.info("Registered provider: DashScope (Qwen cloud)")

    # 1. Gemini — secondary (biggest free cloud tier)
    if config.get("GOOGLE_AI_API_KEY"):
        if GeminiProvider is None:
            logger.warning("Skipping Gemini provider: SDK dependency unavailable.")
        else:
            gemini_model = config.get("GEMINI_MODEL", "gemini-2.0-flash")
            providers.append(GeminiProvider(config["GOOGLE_AI_API_KEY"], model=gemini_model))
            logger.info("Registered provider: Gemini Flash (model=%s)", gemini_model)

    # 2. Groq — fast secondary
    if config.get("GROQ_API_KEY"):
        if GroqProvider is None:
            logger.warning("Skipping Groq provider: SDK dependency unavailable.")
        else:
            providers.append(GroqProvider(config["GROQ_API_KEY"]))
            logger.info("Registered provider: Groq")

    # 3. OpenRouter — free model priority chain
    if config.get("OPENROUTER_API_KEY") and OpenAICompatProvider is not None:
        deprecated_openrouter_models = {
            "google/gemini-2.0-flash-exp:free",
        }

        raw_fallback_models = config.get("OPENROUTER_FALLBACK_MODELS", "")
        configured_priority_models = [
            m.strip()
            for m in raw_fallback_models.replace(";", ",").split(",")
            if m.strip()
        ]
        if not configured_priority_models:
            configured_priority_models = list(OPENROUTER_FREE_MODEL_PRIORITY)

        # Normalize deprecated fallback entries.
        normalized: list[str] = []
        for model in configured_priority_models:
            if model in deprecated_openrouter_models:
                model = "google/gemini-2.0-flash"
            if model and model not in normalized:
                normalized.append(model)
        configured_priority_models = normalized

        openrouter_model = (config.get("OPENROUTER_MODEL") or "").strip()
        if openrouter_model in deprecated_openrouter_models:
            logger.warning(
                "OPENROUTER_MODEL '%s' is deprecated; selecting from priority chain",
                openrouter_model,
            )
            openrouter_model = ""

        if not openrouter_model or openrouter_model == "openrouter/auto":
            if configured_priority_models:
                openrouter_model = configured_priority_models[0]
                fallback_models = configured_priority_models[1:]
            else:
                openrouter_model = "openrouter/auto"
                fallback_models = []
        else:
            fallback_models = [m for m in configured_priority_models if m != openrouter_model]

        providers.append(OpenAICompatProvider(
            api_key=config["OPENROUTER_API_KEY"],
            model=openrouter_model,
            model_candidates=fallback_models,
            base_url="https://openrouter.ai/api/v1",
            provider_name="openrouter",
            daily_limit_override=200,
            rpm_limit_override=20,
            context_limit_override=1_000_000,
            cost_rank_override=2,
        ))
        logger.info(
            "Registered provider: OpenRouter (model=%s, fallback_candidates=%d)",
            openrouter_model,
            len(fallback_models),
        )
        if fallback_models:
            logger.info(
                "OpenRouter model priority: %s",
                " -> ".join([openrouter_model, *fallback_models]),
            )

    # 4. DeepSeek — near-free
    if config.get("OPENROUTER_API_KEY") and OpenAICompatProvider is None:
        logger.warning("Skipping OpenRouter provider: SDK dependency unavailable.")

    if config.get("DEEPSEEK_API_KEY") and OpenAICompatProvider is not None:
        providers.append(OpenAICompatProvider(
            api_key=config["DEEPSEEK_API_KEY"],
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            provider_name="deepseek",
            daily_limit_override=500,
            rpm_limit_override=10,
            context_limit_override=64_000,
            cost_rank_override=3,
        ))
        logger.info("Registered provider: DeepSeek")

    # 5. OpenAI — if credits available
    if config.get("DEEPSEEK_API_KEY") and OpenAICompatProvider is None:
        logger.warning("Skipping DeepSeek provider: SDK dependency unavailable.")

    if config.get("OPENAI_API_KEY") and OpenAICompatProvider is not None:
        providers.append(OpenAICompatProvider(
            api_key=config["OPENAI_API_KEY"],
            model="gpt-4o-mini",
            provider_name="openai",
            daily_limit_override=200,
            rpm_limit_override=10,
            context_limit_override=128_000,
            cost_rank_override=8,
        ))
        logger.info("Registered provider: OpenAI")

    # 6. Claude — if credits available
    if config.get("OPENAI_API_KEY") and OpenAICompatProvider is None:
        logger.warning("Skipping OpenAI provider: SDK dependency unavailable.")

    if config.get("ANTHROPIC_API_KEY"):
        try:
            from .providers.anthropic_ai import AnthropicProvider
            providers.append(AnthropicProvider(config["ANTHROPIC_API_KEY"]))
            logger.info("Registered provider: Claude")
        except ImportError:
            logger.warning("anthropic package not installed, skipping Claude provider")

    # N. Ollama — last-resort fallback (free, laptop-local, but weaker)
    ollama_model = config.get("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:7b")
    providers.append(OllamaProxyProvider(model=ollama_model))
    logger.info("Registered provider: Ollama (model=%s, fallback=last)", ollama_model)

    if not providers:
        logger.error(
            "No AI providers configured! Set at least GOOGLE_AI_API_KEY "
            "in environment variables."
        )

    return providers
