"""Unit tests for MemberAddNode — CMN-C1-696. Covers BL-01, BL-10, BL-12, BL-13, TC-11."""

import pytest

from framework.schemas.agent_status import AgentStatus
from src.nodes import member_add_node as mod
from src.nodes.member_add_node import MemberAddNode


class FakeClient:
    def __init__(self, result=None, exc=None) -> None:
        self._result, self._exc = result, exc

    def add_member(self, group_object_id, user_object_id):
        if self._exc:
            raise self._exc
        return self._result


@pytest.fixture
def emit_spy(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(mod, "emit_trace_event", lambda ev, p, s: calls.append(ev))
    return calls


def _node(fake) -> MemberAddNode:
    n = MemberAddNode({})
    n._graph_client = lambda state: fake
    return n


def _state() -> dict:
    return {"user_object_id": "U-1", "group_object_id": "G-1", "correlation_id": "x"}


def test_added(emit_spy):  # BL-01 (happy path, node-level)
    r = _node(FakeClient(result="added")).execute(_state())
    assert r["status"] == AgentStatus.SUCCESS.value
    assert not isinstance(r["status"], AgentStatus)
    assert r["membership_result"] == "added"
    assert "iam.entra.member_add.executed" in emit_spy  # TC-11


def test_already_member_idempotent(emit_spy):  # BL-10
    r = _node(FakeClient(result="already_member")).execute(_state())
    assert r["status"] == AgentStatus.SUCCESS.value
    assert not isinstance(r["status"], AgentStatus)
    assert r["membership_result"] == "already_member"


def test_missing_ids(emit_spy):  # BL-13
    r = _node(FakeClient(result="added")).execute({"correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_graph_error_no_partial_write(emit_spy):  # BL-12
    r = _node(FakeClient(exc=RuntimeError("500"))).execute(_state())
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)
