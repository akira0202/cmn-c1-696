"""Unit tests for GroupResolveNode — CMN-C1-696. Covers BL-01, BL-06/07/08/09, SEC-S1-a/b, TC-02."""

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus
from src.nodes import group_resolve_node as mod
from src.nodes.group_resolve_node import GroupResolveNode


class FakeClient:
    def __init__(self, groups=None, exc=None) -> None:
        self._groups, self._exc = groups, exc

    def get_groups_by_display_name(self, name):
        if self._exc:
            raise self._exc
        return self._groups or []


@pytest.fixture
def emit_spy(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(mod, "emit_trace_event", lambda ev, p, s: calls.append(ev))
    return calls


def _node(fake, allowed=("Finance-Approvers-JP",)) -> GroupResolveNode:
    n = GroupResolveNode({"allowed_groups": list(allowed)})
    n._graph_client = lambda state: fake
    return n


def test_success(emit_spy):  # BL-01 (happy path, node-level)
    r = _node(FakeClient(groups=[{"id": "G-1"}])).execute(
        {"parsed_group_name": "Finance-Approvers-JP", "correlation_id": "x"}
    )
    assert r["status"] == AgentStatus.SUCCESS.value
    assert not isinstance(r["status"], AgentStatus)
    assert r["group_object_id"] == "G-1"


def test_not_in_allowlist_raises(emit_spy):  # BL-07 / TC-02
    with pytest.raises(SecurityViolationError):
        _node(FakeClient(groups=[{"id": "G"}])).execute({"parsed_group_name": "Random-Team", "correlation_id": "x"})


def test_privileged_blocked_even_if_allowlisted(emit_spy):  # BL-08 / SEC-S1-b
    n = _node(FakeClient(groups=[{"id": "G"}]), allowed=("Global Administrator",))
    with pytest.raises(SecurityViolationError):
        n.execute({"parsed_group_name": "Global Administrator", "correlation_id": "x"})


def test_privileged_block_case_insensitive(emit_spy):
    n = _node(FakeClient(groups=[{"id": "G"}]), allowed=("global administrator",))
    with pytest.raises(SecurityViolationError):
        n.execute({"parsed_group_name": "GLOBAL ADMINISTRATOR", "correlation_id": "x"})


def test_group_not_found(emit_spy):  # BL-06
    r = _node(FakeClient(groups=[])).execute({"parsed_group_name": "Finance-Approvers-JP", "correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_ambiguous_multiple_matches(emit_spy):  # BL-09
    r = _node(FakeClient(groups=[{"id": "G1"}, {"id": "G2"}])).execute(
        {"parsed_group_name": "Finance-Approvers-JP", "correlation_id": "x"}
    )
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_missing_group(emit_spy):
    r = _node(FakeClient(groups=[{"id": "G"}])).execute({"correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)
