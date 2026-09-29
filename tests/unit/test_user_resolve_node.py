"""Unit tests for UserResolveNode — CMN-C1-696. Covers BL-05/12/13."""

import pytest

from framework.schemas.agent_status import AgentStatus
from src.nodes import user_resolve_node as mod
from src.nodes.user_resolve_node import UserResolveNode


class FakeClient:
    def __init__(self, user=None, exc=None) -> None:
        self._user, self._exc = user, exc

    def get_user(self, email):
        if self._exc:
            raise self._exc
        return self._user


@pytest.fixture
def emit_spy(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(mod, "emit_trace_event", lambda ev, p, s: calls.append(ev))
    return calls


def _node(fake) -> UserResolveNode:
    n = UserResolveNode({"entra_tenant_id": "t", "entra_client_id": "c"})
    n._graph_client = lambda state: fake  # bypass ctx.secrets
    return n


def test_success(emit_spy):  # BL-01 (happy path, node-level)
    r = _node(FakeClient(user={"id": "U-1"})).execute({"parsed_user_email": "a@b.co", "correlation_id": "x"})
    assert r["status"] == AgentStatus.SUCCESS.value
    assert not isinstance(r["status"], AgentStatus)
    assert r["user_object_id"] == "U-1"


def test_missing_email(emit_spy):  # BL-13
    r = _node(FakeClient(user={"id": "U-1"})).execute({"correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_user_not_found(emit_spy):  # BL-05
    r = _node(FakeClient(user=None)).execute({"parsed_user_email": "a@b.co", "correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)
    assert "user_not_found" in emit_spy


def test_graph_error(emit_spy):  # BL-12
    r = _node(FakeClient(exc=RuntimeError("500"))).execute({"parsed_user_email": "a@b.co", "correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_display_name_is_carried_from_the_graph_user(emit_spy):
    r = _node(FakeClient(user={"id": "U-1", "displayName": " Suzuki Hanako "})).execute(
        {"parsed_user_email": "a@b.co", "correlation_id": "x"}
    )
    assert r["status"] == AgentStatus.SUCCESS.value
    assert r["user_display_name"] == "Suzuki Hanako"


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", 42, ["Suzuki Hanako"], {"name": "x"}, "x" * 257, "Suzuki\x00Hanako", "Suzuki\nHanako"],
    ids=["absent", "empty", "blank", "int", "list", "dict", "over-limit", "nul", "newline"],
)
def test_unusable_display_name_becomes_none_without_failing_the_resolve(emit_spy, value):
    user = {"id": "U-1"} if value is None else {"id": "U-1", "displayName": value}
    r = _node(FakeClient(user=user)).execute({"parsed_user_email": "a@b.co", "correlation_id": "x"})
    assert r["status"] == AgentStatus.SUCCESS.value
    assert r["user_object_id"] == "U-1"
    assert r["user_display_name"] is None


def test_display_name_at_the_length_limit_is_kept(emit_spy):
    r = _node(FakeClient(user={"id": "U-1", "displayName": "x" * 256})).execute(
        {"parsed_user_email": "a@b.co", "correlation_id": "x"}
    )
    assert r["user_display_name"] == "x" * 256


def test_display_name_is_not_written_on_a_failed_resolve(emit_spy):
    r = _node(FakeClient(user=None)).execute({"parsed_user_email": "a@b.co", "correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert "user_display_name" not in r
