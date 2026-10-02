"""Provider tool-call boundary and tool policy. FR-MOD-04 and FR-PLG-03."""

import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator


class ToolCallError(Exception):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    permission: str
    effect_class: str
    input_schema: dict[str, Any]
    source_version: str

    def __post_init__(self):
        Draft202012Validator.check_schema(self.input_schema)


@dataclass(frozen=True)
class CompletedToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    status: str
    sanitized_summary: str
    artifact_refs: tuple[str, ...] = ()
    structured_fields: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    duration_ms: int = 0
    byte_count: int = 0
    source_version: str = "1.0"
    side_effect_receipt: str | None = None
    truncated: bool = False


def fixture_tool_result(
    name: str, receipt: dict, output: dict | None, source_version: str
) -> ToolResult:
    """Expose only reviewed receipt fields; model-facing prose is deterministic."""
    if name not in {"named", "browser", "oracle"}:
        raise ToolCallError("Fixture tool is not registered")
    status = receipt.get("status")
    if status not in {"PASSED", "FAILED", "TIMEOUT", "INCONCLUSIVE", "ERROR"}:
        raise ToolCallError("Fixture result status is invalid")
    exit_code = receipt.get("exit_code")
    if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
        raise ToolCallError("Fixture exit code is invalid")
    duration = receipt.get("duration_ms", 0)
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        raise ToolCallError("Fixture duration is invalid")
    output_sha256 = receipt.get("output_sha256")
    if output_sha256 is not None and (
        not isinstance(output_sha256, str)
        or len(output_sha256) != 64
        or any(character not in "0123456789abcdef" for character in output_sha256)
    ):
        raise ToolCallError("Fixture output digest is invalid")
    if output is not None and (
        not isinstance(output.get("artifact_ref"), str)
        or not isinstance(output.get("bytes"), int)
        or output["bytes"] < 0
    ):
        raise ToolCallError("Fixture output artifact is invalid")
    return ToolResult(
        status=status,
        sanitized_summary=f"{name} result: {status.lower()}",
        artifact_refs=(output["artifact_ref"],) if output else (),
        structured_fields={"exit_code": exit_code, "output_sha256": output_sha256},
        retryable=False,
        duration_ms=duration,
        byte_count=output["bytes"] if output else 0,
        source_version=source_version,
        side_effect_receipt=None,
        truncated=bool(output and output["truncated"]),
    )


class ToolCallAssembler:
    """Never exposes executable arguments until a provider marks the call complete."""

    def __init__(self, tools: dict[str, ToolDefinition], max_argument_bytes: int = 65536):
        self.tools = tools
        self.max_argument_bytes = max_argument_bytes
        self._buffers: dict[str, str] = {}
        self._finished: set[str] = set()

    def append_delta(self, call_id: str, delta: str) -> None:
        if call_id in self._finished:
            raise ToolCallError("Delta arrived after completion")
        candidate = self._buffers.get(call_id, "") + delta
        if len(candidate.encode("utf-8")) > self.max_argument_bytes:
            raise ToolCallError("Tool arguments exceed size limit")
        self._buffers[call_id] = candidate

    def complete(
        self, call_id: str, name: str, finalized_arguments: str | None = None
    ) -> CompletedToolCall:
        if call_id in self._finished:
            raise ToolCallError("Tool call already completed")
        definition = self.tools.get(name)
        if definition is None:
            raise ToolCallError("Tool is not registered")
        raw = finalized_arguments if finalized_arguments is not None else self._buffers.get(call_id)
        if raw is None:
            raise ToolCallError("No completed arguments were received")
        if len(raw.encode("utf-8")) > self.max_argument_bytes:
            raise ToolCallError("Tool arguments exceed size limit")
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ToolCallError("Completed tool arguments are invalid JSON") from error
        if not isinstance(arguments, dict):
            raise ToolCallError("Tool arguments must be an object")
        errors = list(Draft202012Validator(definition.input_schema).iter_errors(arguments))
        if errors:
            raise ToolCallError("Completed tool arguments do not match the registered schema")
        self._finished.add(call_id)
        self._buffers.pop(call_id, None)
        return CompletedToolCall(call_id=call_id, name=name, arguments=arguments)

    def disconnect(self) -> None:
        """A stream loss discards every incomplete call; no effect may start."""
        self._buffers.clear()


def require_permission(definition: ToolDefinition, granted: set[str]) -> None:
    if definition.permission not in granted:
        raise ToolCallError("Tool permission denied")


NAMED_TEST = ToolDefinition(
    name="run_named_test",
    permission="sandbox.test.execute",
    effect_class="isolated_execution",
    source_version="1.0",
    input_schema={
        "type": "object",
        "properties": {
            "target_name": {"type": "string", "minLength": 1},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
        },
        "required": ["target_name", "timeout_seconds"],
        "additionalProperties": False,
    },
)
