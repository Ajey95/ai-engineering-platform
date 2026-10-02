"""Native non-streaming provider adapters with provider-scoped continuation.

The returned opaque state can include protected reasoning metadata. It must be
encrypted before persistence and must never be shown in review artifacts.
Live qualification is required before any model registry entry is enabled.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from platform_app.telemetry import tracer
from platform_app.tool_broker import CompletedToolCall, ToolCallAssembler, ToolDefinition


class ProviderError(Exception):
    pass


def _count(usage: dict, key: str, required: bool = False) -> int:
    value = usage.get(key)
    if value is None and not required:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProviderError(f"Provider usage field {key} is missing or invalid")
    return value


@dataclass(frozen=True)
class ProviderTurn:
    provider: str
    model: str
    text: str
    calls: tuple[CompletedToolCall, ...]
    stop_reason: str
    usage: dict[str, int]
    opaque_state: dict[str, Any]


def _validated_calls(raw_calls: list[tuple[str, str, str]], tools: dict) -> tuple:
    assembler = ToolCallAssembler(tools)
    return tuple(
        assembler.complete(call_id, name, arguments)
        for call_id, name, arguments in raw_calls
    )


def _require_continuation(previous: ProviderTurn, provider: str, model: str) -> None:
    if previous.provider != provider or previous.opaque_state["request_model"] != model:
        raise ValueError("Cross-provider continuation is forbidden")
    if not previous.calls:
        raise ValueError("Continuation requires pending tool calls")


@tracer.start_as_current_span("provider.http")
def _http_json(client: httpx.Client, url: str, headers: dict, payload: dict) -> dict:
    try:
        response = client.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ProviderError(f"Provider request failed: {type(error).__name__}") from error
    if not isinstance(body, dict):
        raise ProviderError("Provider returned a non-object response")
    return body


class OpenAIResponses:
    provider = "openai"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self.api_key = api_key
        self.client = client or httpx.Client()

    def generate(
        self, model: str, instruction: str, prompt: str,
        tools: dict[str, ToolDefinition], max_output_tokens: int,
        previous: ProviderTurn | None = None, results: dict[str, dict] | None = None,
    ) -> ProviderTurn:
        if previous is None:
            input_items: list | str = prompt
        else:
            _require_continuation(previous, self.provider, model)
            results = results or {}
            if set(results) != {call.call_id for call in previous.calls}:
                raise ValueError("Every tool call requires one result")
            input_items = [*previous.opaque_state["input"], *previous.opaque_state["output"]]
            input_items.extend(
                {"type": "function_call_output", "call_id": call.call_id,
                 "output": json.dumps(results[call.call_id], separators=(",", ":"))}
                for call in previous.calls
            )
        payload = {
            "model": model,
            "instructions": instruction,
            "input": input_items,
            "tools": [
                {"type": "function", "name": tool.name, "description": tool.name,
                 "parameters": tool.input_schema, "strict": False}
                for tool in tools.values()
            ],
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        raw = _http_json(
            self.client, "https://api.openai.com/v1/responses",
            {"Authorization": f"Bearer {self.api_key}"}, payload,
        )
        output = raw.get("output", [])
        if not isinstance(output, list):
            raise ProviderError("OpenAI output is malformed")
        calls = _validated_calls(
            [(item["call_id"], item["name"], item["arguments"])
             for item in output if item.get("type") == "function_call"],
            tools,
        )
        text = "\n".join(
            part.get("text", "")
            for item in output if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text"
        )
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError("OpenAI usage is missing")
        details = usage.get("output_tokens_details") or {}
        input_details = usage.get("input_tokens_details") or {}
        if not isinstance(details, dict) or not isinstance(input_details, dict):
            raise ProviderError("OpenAI usage details are malformed")
        prior_input = input_items if isinstance(input_items, list) else [
            {"role": "user", "content": input_items}
        ]
        return ProviderTurn(
            self.provider, raw.get("model", model), text, calls,
            raw.get("status", "unknown"),
            {"input_tokens": _count(usage, "input_tokens", required=True),
             "output_tokens": _count(usage, "output_tokens", required=True),
             "reasoning_tokens": _count(details, "reasoning_tokens"),
             "cache_read_tokens": _count(input_details, "cached_tokens")},
            {"request_model": model, "input": prior_input, "output": output},
        )


class AnthropicMessages:
    provider = "anthropic"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        if not api_key:
            raise ValueError("Anthropic API key is required")
        self.api_key = api_key
        self.client = client or httpx.Client()

    def generate(
        self, model: str, instruction: str, prompt: str,
        tools: dict[str, ToolDefinition], max_output_tokens: int,
        previous: ProviderTurn | None = None, results: dict[str, dict] | None = None,
    ) -> ProviderTurn:
        if previous is None:
            messages = [{"role": "user", "content": prompt}]
        else:
            _require_continuation(previous, self.provider, model)
            results = results or {}
            if set(results) != {call.call_id for call in previous.calls}:
                raise ValueError("Every tool call requires one result")
            messages = [*previous.opaque_state["messages"]]
            messages.append({"role": "assistant", "content": previous.opaque_state["content"]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call.call_id,
                 "content": json.dumps(results[call.call_id], separators=(",", ":"))}
                for call in previous.calls
            ]})
        payload = {
            "model": model, "system": instruction, "messages": messages,
            "max_tokens": max_output_tokens,
            "tools": [{"name": tool.name, "description": tool.name,
                       "input_schema": tool.input_schema} for tool in tools.values()],
        }
        raw = _http_json(
            self.client, "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}, payload,
        )
        content = raw.get("content", [])
        if not isinstance(content, list):
            raise ProviderError("Anthropic content is malformed")
        calls = _validated_calls(
            [(item["id"], item["name"], json.dumps(item["input"]))
             for item in content if item.get("type") == "tool_use"],
            tools,
        )
        text = "\n".join(item.get("text", "") for item in content
                         if item.get("type") == "text")
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError("Anthropic usage is missing")
        plain_input = _count(usage, "input_tokens", required=True)
        cache_read = _count(usage, "cache_read_input_tokens")
        cache_creation = _count(usage, "cache_creation_input_tokens")
        return ProviderTurn(
            self.provider, raw.get("model", model), text, calls,
            raw.get("stop_reason", "unknown"),
            {"input_tokens": plain_input + cache_read + cache_creation,
             "output_tokens": _count(usage, "output_tokens", required=True),
             "cache_read_tokens": cache_read,
             "cache_creation_tokens": cache_creation},
            {"request_model": model, "messages": messages, "content": content},
        )


class GeminiGenerateContent:
    provider = "google"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        if not api_key:
            raise ValueError("Google API key is required")
        self.api_key = api_key
        self.client = client or httpx.Client()

    def generate(
        self, model: str, instruction: str, prompt: str,
        tools: dict[str, ToolDefinition], max_output_tokens: int,
        previous: ProviderTurn | None = None, results: dict[str, dict] | None = None,
    ) -> ProviderTurn:
        if previous is None:
            contents = [{"role": "user", "parts": [{"text": prompt}]}]
        else:
            _require_continuation(previous, self.provider, model)
            results = results or {}
            if set(results) != {call.call_id for call in previous.calls}:
                raise ValueError("Every tool call requires one result")
            contents = [*previous.opaque_state["contents"]]
            contents.append(previous.opaque_state["model_content"])
            contents.append({"role": "user", "parts": [
                {"functionResponse": {
                    **({} if call.call_id in previous.opaque_state["missing_ids"]
                       else {"id": call.call_id}),
                    "name": call.name, "response": results[call.call_id],
                }} for call in previous.calls
            ]})
        declarations = [
            {"name": tool.name, "description": tool.name,
             "parametersJsonSchema": tool.input_schema}
            for tool in tools.values()
        ]
        payload = {
            "systemInstruction": {"parts": [{"text": instruction}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_output_tokens},
        }
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        raw = _http_json(
            self.client,
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(model, safe='')}:generateContent",
            {"x-goog-api-key": self.api_key}, payload,
        )
        candidates = raw.get("candidates") or []
        if not candidates:
            raise ProviderError("Google returned no candidate")
        candidate = candidates[0]
        model_content = candidate.get("content") or {}
        parts = model_content.get("parts") or []
        raw_calls = []
        missing_ids = []
        for index, part in enumerate(parts):
            call = part.get("functionCall")
            if call:
                # Older responses may omit id. Keep a stable step-local key.
                call_id = call.get("id") or f"part_{index}"
                if "id" not in call:
                    missing_ids.append(call_id)
                raw_calls.append((call_id, call["name"], json.dumps(call.get("args") or {})))
        calls = _validated_calls(raw_calls, tools)
        usage = raw.get("usageMetadata")
        if not isinstance(usage, dict):
            raise ProviderError("Google usage is missing")
        prompt_tokens = _count(usage, "promptTokenCount", required=True)
        candidate_tokens = _count(usage, "candidatesTokenCount", required=True)
        total_tokens = _count(usage, "totalTokenCount", required=True)
        thought_tokens = _count(usage, "thoughtsTokenCount")
        if total_tokens < prompt_tokens or total_tokens < prompt_tokens + candidate_tokens:
            raise ProviderError("Google usage totals are inconsistent")
        return ProviderTurn(
            self.provider, model,
            "\n".join(part["text"] for part in parts if "text" in part), calls,
            candidate.get("finishReason", "unknown"),
            {"input_tokens": prompt_tokens,
             "output_tokens": max(candidate_tokens + thought_tokens,
                                  total_tokens - prompt_tokens),
             "reasoning_tokens": thought_tokens,
             "cache_read_tokens": _count(usage, "cachedContentTokenCount"),
             "total_tokens": total_tokens},
            {"request_model": model, "contents": contents,
             "model_content": model_content, "missing_ids": missing_ids},
        )
