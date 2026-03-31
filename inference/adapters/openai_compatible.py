"""Adapters for OpenAI-compatible chat completion endpoints."""

import json

import structlog
from openai import AsyncOpenAI

from agent.domain.model import CompletionResult, CompletionUsage, ToolCall, ToolDefinition
from conversation.domain.model import Conversation

logger = structlog.get_logger()


def _messages_to_openai(conversation: Conversation) -> list[dict]:
    """Convert domain Conversation to OpenAI-format messages."""
    out: list[dict] = []
    for message in conversation.messages:
        if message.role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            })
        elif message.role == "assistant" and message.tool_calls:
            assistant_message: dict = {"role": "assistant"}
            if message.content:
                assistant_message["content"] = message.content
            assistant_message["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments),
                    },
                }
                for tool_call in message.tool_calls
            ]
            out.append(assistant_message)
        else:
            out.append({"role": message.role, "content": message.content})
    return out


def _tools_to_openai(tools: list[ToolDefinition]) -> list[dict]:
    """Convert domain ToolDefinitions to OpenAI-format tool specs."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def _extract_usage(response) -> CompletionUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)

    return CompletionUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        cached_prompt_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0) or 0,
    )


def _extract_reasoning(choice_message) -> str | None:
    reasoning = getattr(choice_message, "reasoning", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    if isinstance(reasoning, list) and reasoning:
        return json.dumps(reasoning, indent=2, sort_keys=True)

    model_extra = getattr(choice_message, "model_extra", None) or {}
    if not isinstance(model_extra, dict):
        return None

    for key in ("reasoning", "reasoning_details"):
        value = model_extra.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value:
            return json.dumps(value, indent=2, sort_keys=True)

    return None


class OpenAICompatibleAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str = "",
        provider_name: str = "openai_compatible",
    ) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
        )
        self._model = model
        self._system_prompt = system_prompt
        self._provider_name = provider_name
        self._last_debug_snapshot: dict | None = None

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

        self._last_debug_snapshot = {
            "provider": self._provider_name,
            "request": kwargs,
        }

        logger.info(
            "openai_compatible_call",
            provider=self._provider_name,
            model=self._model,
            messages=len(messages),
        )
        response = await self._client.chat.completions.create(**kwargs)
        response_payload = (
            response.model_dump(exclude_none=True)
            if hasattr(response, "model_dump")
            else {"repr": repr(response)}
        )
        self._last_debug_snapshot["response"] = response_payload

        choice = response.choices[0]
        text = choice.message.content or ""
        stop_reason = choice.finish_reason or "stop"
        usage = _extract_usage(response)
        reasoning = _extract_reasoning(choice.message)

        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            for tool_call in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tool_call.id,
                        name=tool_call.function.name,
                        arguments=json.loads(tool_call.function.arguments),
                    )
                )

        logger.info(
            "openai_compatible_done",
            provider=self._provider_name,
            stop_reason=stop_reason,
            tool_calls=len(tool_calls),
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
        )
        return CompletionResult(
            text=text,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            usage=usage,
            reasoning=reasoning,
        )

    def get_last_debug_snapshot(self) -> dict | None:
        return self._last_debug_snapshot
