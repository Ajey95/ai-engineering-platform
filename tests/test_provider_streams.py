"""Controlled native SSE traces keep tool arguments unusable until terminal success."""

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
    name="inspect", permission="repo.read", effect_class="read", source_version="1.0",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"], "additionalProperties": False},
)


def client_for(events: list[dict], requests: list[dict] | None = None):
    body = "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        if "type" in event else f"data: {json.dumps(event)}\n\n"
        for event in events
    )

    def respond(request):
        if requests is not None:
            requests.append(json.loads(request.content))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return httpx.Client(transport=httpx.MockTransport(respond))


def test_openai_stream_waits_for_final_tool_and_usage():
    args = '{"path":"server.py"}'
    output = {"type": "function_call", "id": "fc_1", "call_id": "call_1",
              "name": "inspect", "arguments": args}
    requests = []
    adapter = OpenAIResponses("test-only", client_for([
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1",
         "delta": '{"path":'},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1",
         "delta": '"server.py"}'},
        {"type": "response.function_call_arguments.done", "item_id": "fc_1",
         "name": "inspect", "arguments": args},
        {"type": "response.completed", "response": {
            "model": "model-a", "status": "completed", "output": [output],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }},
    ], requests))
    turn = adapter.generate("model-a", "Inspect", "Find bug", {"inspect": TOOL},
                            100, stream=True)
    assert turn.calls[0].call_id == "call_1"
    assert turn.calls[0].arguments == {"path": "server.py"}
    assert turn.usage["input_tokens"] == 10
    assert requests[0]["stream"] is True


@pytest.mark.parametrize("events", [
    [{"type": "response.function_call_arguments.delta", "item_id": "fc_1",
      "delta": '{"path":'}],
    [{"type": "response.function_call_arguments.done", "item_id": "fc_1",
      "name": "inspect", "arguments": '{"path":"server.py"}'},
     {"type": "response.completed", "response": {
         "model": "model-a", "status": "completed", "output": [],
         "usage": {"input_tokens": 10, "output_tokens": 5},
     }}],
])
def test_openai_interrupted_or_mismatched_stream_has_no_turn(events):
    adapter = OpenAIResponses("test-only", client_for(events))
    with pytest.raises(ProviderError) as failure:
        adapter.generate("model-a", "i", "p", {"inspect": TOOL}, 100, stream=True)
    assert failure.value.code == "PROVIDER_STREAM_INTERRUPTED"


def test_openai_completed_unreviewed_stream_tool_is_denied():
    adapter = OpenAIResponses("test-only", client_for([
        {"type": "response.function_call_arguments.done", "item_id": "fc-attack",
         "name": "publish_code", "arguments": '{"token":"private-value"}'},
        {"type": "response.completed", "response": {
            "model": "model-a", "status": "completed", "output": [{
                "type": "function_call", "id": "fc-attack", "call_id": "call-attack",
                "name": "publish_code", "arguments": '{"token":"private-value"}',
            }], "usage": {"input_tokens": 10, "output_tokens": 5},
        }},
    ]))
    with pytest.raises(ProviderError) as denied:
        adapter.generate("model-a", "Do not publish", "Untrusted page content", {}, 100,
                         stream=True)
    assert denied.value.code == "PROVIDER_TOOL_DENIED"
    assert "private-value" not in str(denied.value)


def test_anthropic_stream_preserves_tool_id_and_completed_json():
    requests = []
    adapter = AnthropicMessages("test-only", client_for([
        {"type": "message_start", "message": {"model": "claude-test",
         "usage": {"input_tokens": 10, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": "toolu_1", "name": "inspect", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {
            "type": "input_json_delta", "partial_json": '{"path":'}},
        {"type": "content_block_delta", "index": 0, "delta": {
            "type": "input_json_delta", "partial_json": '"server.py"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ], requests))
    turn = adapter.generate("claude-test", "i", "p", {"inspect": TOOL}, 100,
                            stream=True)
    assert turn.calls[0].call_id == "toolu_1"
    assert turn.calls[0].arguments == {"path": "server.py"}
    assert turn.opaque_state["content"][0]["input"] == {"path": "server.py"}
    assert requests[0]["stream"] is True


def test_anthropic_missing_message_stop_rejects_partial_tool():
    adapter = AnthropicMessages("test-only", client_for([
        {"type": "message_start", "message": {"model": "claude-test",
         "usage": {"input_tokens": 10, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": "toolu_1", "name": "inspect", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {
            "type": "input_json_delta", "partial_json": '{"path":'}},
    ]))
    with pytest.raises(ProviderError) as failure:
        adapter.generate("claude-test", "i", "p", {"inspect": TOOL}, 100, stream=True)
    assert failure.value.code == "PROVIDER_STREAM_INTERRUPTED"


def test_google_stream_requires_finish_and_usage_and_preserves_signature():
    requests = []
    adapter = GeminiGenerateContent("test-only", client_for([
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Checking"}]}}]},
        {"modelVersion": "gemini-test-snapshot", "candidates": [{"content": {
            "role": "model", "parts": [{
            "functionCall": {"id": "call_1", "name": "inspect",
                             "args": {"path": "server.py"}},
            "thoughtSignature": "opaque",
        }]}, "finishReason": "STOP"}],
         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4,
                           "totalTokenCount": 14}},
    ], requests))
    turn = adapter.generate("gemini-test", "i", "p", {"inspect": TOOL}, 100,
                            stream=True)
    assert turn.text == "Checking"
    assert turn.model == "gemini-test-snapshot"
    assert turn.calls[0].call_id == "call_1"
    assert turn.opaque_state["model_content"]["parts"][1]["thoughtSignature"] == "opaque"
    assert requests[0]["contents"][0]["parts"][0]["text"] == "p"


def test_google_stream_without_finish_is_not_a_completed_turn():
    adapter = GeminiGenerateContent("test-only", client_for([
        {"candidates": [{"content": {"parts": [{"text": "partial"}]}}],
         "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1,
                           "totalTokenCount": 2}},
    ]))
    with pytest.raises(ProviderError) as failure:
        adapter.generate("gemini-test", "i", "p", {}, 10, stream=True)
    assert failure.value.code == "PROVIDER_STREAM_INTERRUPTED"


def test_stream_transport_failure_does_not_expose_private_detail():
    def disconnect(_request):
        raise httpx.ReadError("secret transport detail")

    client = httpx.Client(transport=httpx.MockTransport(disconnect))
    with pytest.raises(ProviderError) as failure:
        OpenAIResponses("test-only", client).generate(
            "model-a", "i", "p", {}, 10, stream=True,
        )
    assert failure.value.code == "PROVIDER_STREAM_INTERRUPTED"
    assert "secret transport detail" not in str(failure.value)
