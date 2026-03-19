"""Dumb adapter — calls OpenRouter's OpenAI-compatible API directly."""

import json
import os

import structlog
from openai import AsyncOpenAI

from agent.domain.model import CompletionResult, ToolCall, ToolDefinition
from conversation.domain.model import Conversation

logger = structlog.get_logger()

DEFAULT_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _messages_to_openai(conversation: Conversation) -> list[dict]:
    """Convert domain Conversation to OpenAI-format messages."""
    out: list[dict] = []
    for m in conversation.messages:
        if m.role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id,
                "content": m.content,
            })
        elif m.role == "assistant" and m.tool_calls:
            msg: dict = {"role": "assistant"}
            if m.content:
                msg["content"] = m.content
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in m.tool_calls
            ]
            out.append(msg)
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _tools_to_openai(tools: list[ToolDefinition]) -> list[dict]:
    """Convert domain ToolDefinitions to OpenAI-format tool specs."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


class OpenRouterAdapter:
    def __init__(self, model: str | None = None, system_prompt: str = "") -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self._client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
        )
        self._model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
        self._system_prompt = system_prompt

    async def complete(
        self,
        conversation: Conversation,
        tools: list[ToolDefinition],
    ) -> CompletionResult:
        messages = _messages_to_openai(conversation)
        if self._system_prompt:
            messages = [{"role": "system", "content": self._system_prompt}] + messages
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = _tools_to_openai(tools)

        logger.info("openrouter_call", model=self._model, messages=len(messages))
        response = await self._client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        text = choice.message.content or ""
        stop_reason = choice.finish_reason or "stop"

        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        logger.info("openrouter_done", stop_reason=stop_reason, tool_calls=len(tool_calls))
        return CompletionResult(text=text, stop_reason=stop_reason, tool_calls=tool_calls)
