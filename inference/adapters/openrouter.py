"""OpenRouter adapter built on the generic OpenAI-compatible adapter."""

import os

from inference.adapters.openai_compatible import OpenAICompatibleAdapter

DEFAULT_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAdapter(OpenAICompatibleAdapter):
    def __init__(self, model: str | None = None, system_prompt: str = "") -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        super().__init__(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            model=model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
            system_prompt=system_prompt,
            provider_name="openrouter",
        )
