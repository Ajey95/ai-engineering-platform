"""Bounded native SSE assembly; no tool call leaves this module before final completion."""

from __future__ import annotations

import json
from collections.abc import Iterable

from platform_app.tool_broker import ToolCallAssembler, ToolCallError, ToolDefinition


class StreamProtocolError(Exception):
    pass


def _valid_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise StreamProtocolError(f"{label} is malformed")
    return value


def assemble_openai(events: Iterable[dict], tools: dict[str, ToolDefinition]) -> dict:
    """The terminal Responses object is authoritative, after delta boundary checks."""
    assembler = ToolCallAssembler(tools)
    buffers: dict[str, str] = {}
    completed: dict[str, tuple[str, str]] = {}
    terminal: dict | None = None
    for event in events:
        kind = event.get("type")
        if terminal is not None:
            raise StreamProtocolError("OpenAI emitted data after completion")
        if kind == "response.function_call_arguments.delta":
            item_id, delta = event.get("item_id"), event.get("delta")
            if not isinstance(item_id, str) or not isinstance(delta, str):
                raise StreamProtocolError("OpenAI tool delta is malformed")
            try:
                assembler.append_delta(item_id, delta)
            except ToolCallError as error:
                raise StreamProtocolError("OpenAI tool delta is invalid") from error
            buffers[item_id] = buffers.get(item_id, "") + delta
        elif kind == "response.function_call_arguments.done":
            item_id, name, arguments = (
                event.get("item_id"), event.get("name"), event.get("arguments")
            )
            if not all(isinstance(item, str) for item in (item_id, name, arguments)):
                raise StreamProtocolError("OpenAI tool completion is malformed")
            if item_id in buffers and buffers[item_id] != arguments:
                raise StreamProtocolError("OpenAI tool arguments changed after streaming")
            try:
                assembler.complete(item_id, name, arguments)
            except ToolCallError as error:
                raise StreamProtocolError("OpenAI completed tool call is invalid") from error
            completed[item_id] = (name, arguments)
        elif kind == "response.completed":
            if terminal is not None:
                raise StreamProtocolError("OpenAI response completed twice")
            terminal = _valid_object(event.get("response"), "OpenAI terminal response")
        elif kind in {"response.failed", "response.incomplete", "error"}:
            raise StreamProtocolError("OpenAI stream did not complete successfully")
    if terminal is None or terminal.get("status") != "completed":
        raise StreamProtocolError("OpenAI stream ended without completion")
    output = terminal.get("output")
    if not isinstance(output, list):
        raise StreamProtocolError("OpenAI terminal output is malformed")
    final_calls = {}
    for item in output:
        if isinstance(item, dict) and item.get("type") == "function_call":
            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id in final_calls:
                raise StreamProtocolError("OpenAI terminal tool item is invalid")
            final_calls[item_id] = (item.get("name"), item.get("arguments"))
    if completed != final_calls:
        raise StreamProtocolError("OpenAI stream has incomplete tool calls")
    return terminal


def assemble_anthropic(events: Iterable[dict], tools: dict[str, ToolDefinition]) -> dict:
    """A tool_use block is executable only after its stop and the message stop."""
    assembler = ToolCallAssembler(tools)
    blocks: dict[int, dict] = {}
    open_blocks: set[int] = set()
    message: dict | None = None
    usage: dict = {}
    stop_reason: str | None = None
    stopped = False
    for event in events:
        kind = event.get("type")
        if stopped and kind != "ping":
            raise StreamProtocolError("Anthropic emitted data after message stop")
        if message is None and kind not in {"message_start", "ping"}:
            raise StreamProtocolError("Anthropic content preceded message start")
        if kind == "message_start":
            if message is not None:
                raise StreamProtocolError("Anthropic message started twice")
            message = _valid_object(event.get("message"), "Anthropic message")
            usage = _valid_object(message.get("usage"), "Anthropic input usage").copy()
        elif kind == "content_block_start":
            index = event.get("index")
            if not isinstance(index, int) or index < 0 or index in blocks:
                raise StreamProtocolError("Anthropic block index is invalid")
            block = _valid_object(event.get("content_block"), "Anthropic block").copy()
            if block.get("type") == "fallback":
                raise StreamProtocolError("Anthropic serving model changed midstream")
            if block.get("type") == "tool_use":
                block["_json_deltas"] = ""
            blocks[index] = block
            open_blocks.add(index)
        elif kind == "content_block_delta":
            index = event.get("index")
            if index not in open_blocks:
                raise StreamProtocolError("Anthropic delta has no open block")
            delta = _valid_object(event.get("delta"), "Anthropic delta")
            block = blocks[index]
            if delta.get("type") == "text_delta" and block.get("type") == "text":
                value = delta.get("text")
                if not isinstance(value, str):
                    raise StreamProtocolError("Anthropic text delta is invalid")
                block["text"] = block.get("text", "") + value
            elif delta.get("type") == "input_json_delta" and block.get("type") == "tool_use":
                value = delta.get("partial_json")
                call_id = block.get("id")
                if not isinstance(value, str) or not isinstance(call_id, str):
                    raise StreamProtocolError("Anthropic tool delta is invalid")
                try:
                    assembler.append_delta(call_id, value)
                except ToolCallError as error:
                    raise StreamProtocolError("Anthropic tool delta exceeds policy") from error
                block["_json_deltas"] += value
            elif delta.get("type") == "signature_delta" and block.get("type") == "thinking":
                block["signature"] = delta.get("signature")
            elif delta.get("type") == "thinking_delta" and block.get("type") == "thinking":
                block["thinking"] = block.get("thinking", "") + str(delta.get("thinking", ""))
        elif kind == "content_block_stop":
            index = event.get("index")
            if index not in open_blocks:
                raise StreamProtocolError("Anthropic block stopped without start")
            open_blocks.remove(index)
            block = blocks[index]
            if block.get("type") == "tool_use":
                raw = block.pop("_json_deltas")
                if not raw:
                    raw = json.dumps(block.get("input", {}), separators=(",", ":"))
                try:
                    completed = assembler.complete(block["id"], block["name"], raw)
                except (KeyError, ToolCallError) as error:
                    raise StreamProtocolError("Anthropic completed tool call is invalid") from error
                block["input"] = completed.arguments
        elif kind == "message_delta":
            delta = _valid_object(event.get("delta"), "Anthropic message delta")
            if isinstance(delta.get("stop_reason"), str):
                stop_reason = delta["stop_reason"]
            usage.update(_valid_object(event.get("usage"), "Anthropic output usage"))
        elif kind == "message_stop":
            stopped = True
        elif kind == "error":
            raise StreamProtocolError("Anthropic stream returned an error")
    if message is None or not stopped or open_blocks or stop_reason is None:
        raise StreamProtocolError("Anthropic stream ended without completion")
    if sorted(blocks) != list(range(len(blocks))):
        raise StreamProtocolError("Anthropic content block sequence is incomplete")
    if any(block.get("type") == "tool_use" for block in blocks.values()) and (
        stop_reason != "tool_use"
    ):
        raise StreamProtocolError("Anthropic tool call has no tool-use stop reason")
    return {
        "model": message.get("model"), "content": [blocks[index] for index in sorted(blocks)],
        "usage": usage, "stop_reason": stop_reason,
    }


def assemble_google(events: Iterable[dict]) -> dict:
    """GenerateContent chunks carry complete function calls; require finish and usage."""
    parts: list[dict] = []
    usage: dict | None = None
    finish_reason: str | None = None
    model_version: str | None = None
    for chunk in events:
        if "error" in chunk:
            raise StreamProtocolError("Google stream returned an error")
        candidates = chunk.get("candidates") or []
        if candidates:
            if not isinstance(candidates, list) or not isinstance(candidates[0], dict):
                raise StreamProtocolError("Google stream candidate is malformed")
            candidate = candidates[0]
            content = _valid_object(candidate.get("content") or {}, "Google content")
            new_parts = content.get("parts") or []
            if not isinstance(new_parts, list) or any(not isinstance(p, dict) for p in new_parts):
                raise StreamProtocolError("Google stream parts are malformed")
            parts.extend(new_parts)
            if isinstance(candidate.get("finishReason"), str):
                finish_reason = candidate["finishReason"]
        if "usageMetadata" in chunk:
            usage = _valid_object(chunk["usageMetadata"], "Google usage")
        if "modelVersion" in chunk:
            version = chunk["modelVersion"]
            if not isinstance(version, str) or not version or (
                model_version is not None and model_version != version
            ):
                raise StreamProtocolError("Google serving model changed midstream")
            model_version = version
    if finish_reason is None or usage is None:
        raise StreamProtocolError("Google stream ended without completion or usage")
    if any("functionCall" in part for part in parts) and finish_reason != "STOP":
        raise StreamProtocolError("Google tool call did not finish normally")
    return {
        "candidates": [{"content": {"role": "model", "parts": parts},
                        "finishReason": finish_reason}],
        "usageMetadata": usage, **({"modelVersion": model_version} if model_version else {}),
    }
