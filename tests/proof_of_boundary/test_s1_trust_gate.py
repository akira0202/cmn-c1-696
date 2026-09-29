"""PB / S-1 boundary — every domain node requires an INTERNAL caller.

The group membership write is protected by the caller's trust level, and docs/02 / docs/07 state
the consequence: a caller below INTERNAL — including every Marketplace invocation, whose facade
fixes caller trust at VERIFIED_EXTERNAL — is refused before any Graph call. These tests pin that
statement at three levels:

  * each domain node, through ``node(state)`` (``BaseNode.__call__``), never reaches ``execute()``
    for a caller below INTERNAL;
  * the compiled graph, invoked as a VERIFIED_EXTERNAL caller, never reaches the LLM or the
    Microsoft Graph client and performs no membership write;
  * the standalone entry point maps its two caller credentials exactly: the invoke token reaches
    VERIFIED_EXTERNAL only, and INTERNAL is reached only with the separate internal runner token.
"""

import importlib
import sys

import pytest
from fastapi import HTTPException

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import EntraIDGroupAssignAgentGraph
from src.nodes.confirmation_format_node import ConfirmationFormatNode
from src.nodes.group_resolve_node import GroupResolveNode
from src.nodes.input_parse_node import InputParseNode
from src.nodes.member_add_node import MemberAddNode
from src.nodes.user_resolve_node import UserResolveNode
from src.services.stage5_fakes import FAKE_GROUP_NAME, FakeExtractionLLM, FakeGraphApiClient

DOMAIN_NODES = (InputParseNode, UserResolveNode, GroupResolveNode, MemberAddNode, ConfirmationFormatNode)
BELOW_INTERNAL = (TrustLevel.ANONYMOUS, TrustLevel.VERIFIED_EXTERNAL)


@pytest.mark.parametrize("node_cls", DOMAIN_NODES, ids=lambda c: c.__name__)
def test_every_domain_node_declares_internal(node_cls):
    assert node_cls.required_trust_level is TrustLevel.INTERNAL


@pytest.mark.parametrize("caller", BELOW_INTERNAL, ids=lambda t: t.value)
@pytest.mark.parametrize("node_cls", DOMAIN_NODES, ids=lambda c: c.__name__)
def test_caller_below_internal_never_reaches_execute(node_cls, caller, monkeypatch):
    executed: list[str] = []

    def spy(self, state):
        executed.append(type(self).__name__)
        return {}

    monkeypatch.setattr(node_cls, "execute", spy)

    result = node_cls()({"caller_trust_level": caller.value, "correlation_id": "s1-trust-gate-test"})

    assert executed == []
    assert result["status"] == "error"
    assert any("S-1 trust gate denied" in entry for entry in result["error_log"])


def test_internal_caller_reaches_execute(monkeypatch):
    """Control case: the same call with an INTERNAL caller does reach execute(), so the refusal
    above is the trust gate and not a harness that never calls the node."""
    executed: list[str] = []

    def spy(self, state):
        executed.append(type(self).__name__)
        return {}

    monkeypatch.setattr(InputParseNode, "execute", spy)

    InputParseNode()({"caller_trust_level": TrustLevel.INTERNAL.value, "correlation_id": "s1-trust-gate-test"})

    assert executed == ["InputParseNode"]


def _graph(llm, client):
    graph = EntraIDGroupAssignAgentGraph(
        config={
            "entra_tenant_id": "t",
            "entra_client_id": "c",
            "allowed_groups": [FAKE_GROUP_NAME],
            "require_justification": True,
            "llm": llm,
            "graph_client_factory": lambda state: client,
        }
    )
    graph.compile()
    return graph


@pytest.mark.parametrize("caller", BELOW_INTERNAL, ids=lambda t: t.value)
def test_graph_invoked_below_internal_makes_no_llm_or_graph_call(caller):
    llm = FakeExtractionLLM()
    client = FakeGraphApiClient()

    out = _graph(llm, client).invoke(
        "Add the user to the group",
        ctx=InvocationContext(session_id="s1", caller_trust_level=caller, caller_id="caller"),
    )

    assert out["status"] == "error"
    assert llm.calls == []
    assert client.get_user_calls == []
    assert client.get_groups_calls == []
    assert client.add_member_calls == []


def test_graph_invoked_as_internal_performs_the_write():
    """Control case for the graph-level refusal above."""
    llm = FakeExtractionLLM()
    client = FakeGraphApiClient()

    out = _graph(llm, client).invoke(
        "Add the user to the group",
        ctx=InvocationContext(session_id="s1", caller_trust_level=TrustLevel.INTERNAL, caller_id="caller"),
    )

    assert out["status"] == "success"
    assert len(client.add_member_calls) == 1


@pytest.fixture
def resolve(monkeypatch):
    monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")
    sys.modules.pop("src.api.server", None)
    yield importlib.import_module("src.api.server")._resolve_standalone_trust
    sys.modules.pop("src.api.server", None)


INVOKE = "invoke-token-for-test"
RUNNER = "runner-token-for-test"


def test_internal_runner_token_maps_to_internal(resolve):
    assert resolve(TrustLevel.ANONYMOUS, f"Bearer {RUNNER}", INVOKE, RUNNER) is TrustLevel.INTERNAL


def test_invoke_token_maps_to_verified_external_only(resolve):
    assert resolve(TrustLevel.ANONYMOUS, f"Bearer {INVOKE}", INVOKE, RUNNER) is TrustLevel.VERIFIED_EXTERNAL


def test_invoke_token_never_reaches_internal_without_a_runner_token(resolve):
    assert resolve(TrustLevel.ANONYMOUS, f"Bearer {INVOKE}", INVOKE, None) is TrustLevel.VERIFIED_EXTERNAL


def test_wrong_token_is_rejected_when_a_token_is_configured(resolve):
    with pytest.raises(HTTPException) as exc:
        resolve(TrustLevel.ANONYMOUS, "Bearer wrong", INVOKE, RUNNER)
    assert exc.value.status_code == 401


def test_no_token_configured_leaves_the_caller_anonymous(resolve):
    assert resolve(TrustLevel.ANONYMOUS, "", None, None) is TrustLevel.ANONYMOUS


def test_middleware_established_trust_is_not_changed(resolve):
    assert resolve(TrustLevel.VERIFIED_EXTERNAL, f"Bearer {RUNNER}", INVOKE, RUNNER) is TrustLevel.VERIFIED_EXTERNAL


# The internal runner credential is honoured only when the process was built with the
# bounded-fake profile, where the Graph client is an in-process fake. A process built for a
# real deployment ignores it even if it is set, so it can never open a path to a real write.


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("STG_INTERNAL_RUNNER_TOKEN", RUNNER)
    sys.modules.pop("src.api.server", None)
    yield importlib.import_module("src.api.server")
    sys.modules.pop("src.api.server", None)


def _post_as_runner(module):
    from fastapi.testclient import TestClient

    client = module.agent.config["graph_client_factory"](None)
    before = len(client.add_member_calls)
    response = TestClient(module.app).post(
        "/invoke", json={"input": "Add the user to the group"}, headers={"Authorization": f"Bearer {RUNNER}"}
    )
    return response, len(client.add_member_calls) - before


def test_runner_token_is_honoured_under_the_bounded_fake_profile(server):
    assert server._internal_runner_token() == RUNNER

    response, writes = _post_as_runner(server)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert writes == 1


def test_runner_token_is_ignored_when_the_process_was_not_built_with_the_fake_profile(server, monkeypatch):
    monkeypatch.setattr(server, "_BOUNDED_FAKE_PROFILE", False)

    assert server._internal_runner_token() is None

    response, writes = _post_as_runner(server)

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert writes == 0


def test_runner_token_is_rejected_when_only_the_invoke_token_is_honoured(server, monkeypatch):
    monkeypatch.setattr(server, "_BOUNDED_FAKE_PROFILE", False)
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", INVOKE)

    response, writes = _post_as_runner(server)

    assert response.status_code == 401
    assert writes == 0


def test_the_profile_is_fixed_at_startup_not_re_read_per_request(server, monkeypatch):
    """The environment changing after start must not change which credential is honoured."""
    monkeypatch.setattr(server, "_BOUNDED_FAKE_PROFILE", False)
    monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")

    assert server._internal_runner_token() is None
