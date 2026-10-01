import pytest

from platform_app.tool_broker import NAMED_TEST, ToolCallAssembler, ToolCallError


def assembler():
    return ToolCallAssembler({NAMED_TEST.name: NAMED_TEST})


def test_partial_arguments_cannot_execute_after_disconnect():
    calls = assembler()
    calls.append_delta("call-1", '{"target_name":"unit",')
    calls.disconnect()
    with pytest.raises(ToolCallError, match="No completed arguments"):
        calls.complete("call-1", "run_named_test")


def test_complete_schema_validated_call_is_exposed_once():
    calls = assembler()
    calls.append_delta("call-1", '{"target_name":"unit",')
    calls.append_delta("call-1", '"timeout_seconds":30}')
    complete = calls.complete("call-1", "run_named_test")
    assert complete.arguments == {"target_name": "unit", "timeout_seconds": 30}
    with pytest.raises(ToolCallError, match="already completed"):
        calls.complete("call-1", "run_named_test", "{}")


def test_unregistered_or_invalid_call_is_denied():
    calls = assembler()
    with pytest.raises(ToolCallError, match="not registered"):
        calls.complete("call-1", "delete_database", "{}")
    with pytest.raises(ToolCallError, match="registered schema"):
        calls.complete("call-2", "run_named_test", '{"target_name":"unit","timeout_seconds":301}')
