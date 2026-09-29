"""Unit tests for InputParseNode — CMN-C1-696. Covers BL-01, BL-02, BL-03, BL-04, TC-02, TC-09, TC-11."""

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus
from src.nodes import input_parse_node as mod
from src.nodes.input_parse_node import InputParseNode


class FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, prompt: str, state: dict) -> dict:
        return {"content": self._content}


@pytest.fixture
def emit_spy(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(mod, "emit_trace_event", lambda ev, payload, state: calls.append(ev))
    return calls


def _node(content: str, require_justification: bool = False) -> InputParseNode:
    return InputParseNode(FakeLLM(content), {"require_justification": require_justification})


def test_happy_path(emit_spy):  # BL-01 (happy path, node-level)
    node = _node('{"email":"a@b.co","group_name":"Finance-Approvers-JP","justification":"new role"}')
    r = node.execute({"user_input": "Add a@b.co to Finance-Approvers-JP for new role", "correlation_id": "x"})
    assert r["status"] == AgentStatus.SUCCESS.value
    assert not isinstance(r["status"], AgentStatus)
    assert r["parsed_user_email"] == "a@b.co"
    assert r["parsed_group_name"] == "Finance-Approvers-JP"
    assert r["business_justification"] == "new role"
    assert "input_parsed" in emit_spy  # TC-11: domain event on success path


def test_invalid_email_raises(emit_spy):
    node = _node('{"email":"not-an-email","group_name":"G"}')
    with pytest.raises(SecurityViolationError):  # BL-02 / TC-02
        node.execute({"user_input": "garbage", "correlation_id": "x"})
    assert emit_spy  # audited before raising


def test_email_null_raises(emit_spy):
    node = _node('{"email":null,"group_name":"G"}')
    with pytest.raises(SecurityViolationError):  # BL-02
        node.execute({"user_input": "no email", "correlation_id": "x"})


def test_missing_group_errors(emit_spy):
    node = _node('{"email":"a@b.co","group_name":null}')
    r = node.execute({"user_input": "add a@b.co somewhere", "correlation_id": "x"})  # BL-04
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_missing_justification_errors(emit_spy):
    node = _node('{"email":"a@b.co","group_name":"G","justification":null}', require_justification=True)
    r = node.execute({"user_input": "add a@b.co to G group", "correlation_id": "x"})  # BL-03
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)
    assert "justification" in r["error_log"][0].lower()


def test_empty_input_errors(emit_spy):
    r = _node("{}").execute({"user_input": "   ", "correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_llm_unparseable_errors(emit_spy):
    r = _node("sorry, I cannot help").execute({"user_input": "add a@b.co to G group", "correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)
