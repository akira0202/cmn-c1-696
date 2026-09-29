"""Unit tests for the provisional Stage 5 bounded-fake profile — CMN-C1-696.

The provisional Stage 5 job boots this template in CI and performs one first invoke. The
agent's core step is a privileged Entra ID membership write, so that invoke runs against the
bounded fakes instead of a live tenant. These tests pin what the fake profile must prove — the
workflow contract still executes for real — and what it must never do: be reachable from a
normal deployment, or leak its synthetic values into the audit trail as if they were real.

The fakes are injected directly here through the constructor parameters. The CI environment
flag is exercised only in the two tests that assert profile selection itself.
"""

import pytest

from framework.schemas.agent_status import AgentStatus
from src.api import startup as startup_mod
from src.nodes import input_parse_node as input_parse_mod
from src.nodes import member_add_node as member_add_mod
from src.nodes.input_parse_node import InputParseNode
from src.nodes.member_add_node import MemberAddNode
from src.nodes.confirmation_format_node import ConfirmationFormatNode
from src.services.stage5_fakes import (
    FAKE_GROUP_NAME,
    FAKE_GROUP_OBJECT_ID,
    FAKE_USER_EMAIL,
    FAKE_USER_OBJECT_ID,
    FakeExtractionLLM,
    FakeGraphApiClient,
)


@pytest.fixture
def emit_calls(monkeypatch):
    """Capture (event, payload) so a test can assert on what reaches the audit trail."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(member_add_mod, "emit_trace_event", lambda ev, p, s: calls.append((ev, p)))
    return calls


def _resolved_state() -> dict:
    return {
        "user_object_id": FAKE_USER_OBJECT_ID,
        "group_object_id": FAKE_GROUP_OBJECT_ID,
        "correlation_id": "stage5-bounded-fake",
    }


def _node(fake: FakeGraphApiClient) -> MemberAddNode:
    return MemberAddNode(node_config={}, client_factory=lambda state: fake)


class TestTheFakeLlmIsCallableByTheNodeItStandsInFor:
    """The fake must satisfy the surface `InputParseNode` actually calls.

    `FakeExtractionLLM` previously declared ``complete(self, prompt)`` while the node calls
    ``complete(prompt, state)`` positionally. Nothing combined the two — the node's own unit
    tests use a separate double that had the right signature — so the mismatch surfaced only
    when the profile was exercised for real, as a TypeError inside execute(). These tests
    drive the node with the profile's own object so the surfaces cannot drift apart again.
    """

    @staticmethod
    def _state() -> dict:
        return {
            "user_input": "add someone to a group",
            "correlation_id": "stage5-bounded-fake",
            "session_id": "stage5-bounded-fake",
            "thread_id": "stage5-bounded-fake",
            "trace_id": "stage5-bounded-fake",
        }

    def test_node_can_call_the_profiles_llm(self, monkeypatch):
        monkeypatch.setattr(input_parse_mod, "emit_trace_event", lambda ev, p, st: None)
        monkeypatch.setattr(input_parse_mod, "emit_progress", lambda *a, **k: None)
        llm = FakeExtractionLLM()
        delta = InputParseNode(llm, node_config={}).execute(self._state())
        assert delta.get("status") != AgentStatus.ERROR.value, delta.get("error_log")
        assert delta["parsed_user_email"] == FAKE_USER_EMAIL
        assert delta["parsed_group_name"] == FAKE_GROUP_NAME

    def test_the_state_argument_is_accepted_positionally(self):
        """The node passes state positionally; a keyword-only or absent parameter breaks it."""
        FakeExtractionLLM().complete("prompt", {"correlation_id": "x"})


class TestWriteIsActuallyPerformed:
    """The fake must not simply report success — the node has to reach the write."""

    def test_add_member_called_once_with_resolved_ids(self, emit_calls):
        fake = FakeGraphApiClient()
        result = _node(fake).execute(_resolved_state())

        assert result["status"] == AgentStatus.SUCCESS.value
        assert fake.add_member_calls == [(FAKE_GROUP_OBJECT_ID, FAKE_USER_OBJECT_ID)]

    def test_no_write_attempted_when_resolution_incomplete(self, emit_calls):
        fake = FakeGraphApiClient()
        result = _node(fake).execute({"correlation_id": "stage5-bounded-fake"})

        assert result["status"] == AgentStatus.ERROR.value
        assert fake.add_member_calls == []


class TestBothWriteOutcomes:
    """`added` and `already_member` are the two contract values of the real client."""

    @pytest.mark.parametrize("outcome", ["added", "already_member"])
    def test_audit_record_carries_the_outcome(self, emit_calls, outcome):
        fake = FakeGraphApiClient(add_result=outcome)
        result = _node(fake).execute(_resolved_state())

        assert result["membership_result"] == outcome
        executed = [p for ev, p in emit_calls if ev == "iam.entra.member_add.executed"]
        assert len(executed) == 1
        assert executed[0]["add_result"] == outcome
        assert executed[0]["user_object_id"] == FAKE_USER_OBJECT_ID
        assert executed[0]["group_id"] == FAKE_GROUP_OBJECT_ID

    @pytest.mark.parametrize("outcome", ["added", "already_member"])
    def test_confirmation_is_built_for_both_outcomes(self, monkeypatch, outcome):
        import src.nodes.confirmation_format_node as confirm_mod

        monkeypatch.setattr(confirm_mod, "emit_trace_event", lambda ev, p, s: None)
        state = {
            **_resolved_state(),
            "membership_result": outcome,
            "parsed_user_email": FAKE_USER_EMAIL,
            "parsed_group_name": FAKE_GROUP_NAME,
            "business_justification": "ci",
        }
        result = ConfirmationFormatNode({}).execute(state)

        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["confirmation"]["result"] == outcome


class TestFailurePathIsNotDisguised:
    """A failing fake must surface as an error, never as a fabricated success."""

    def test_client_exception_yields_error_and_no_partial_output(self, emit_calls):
        fake = FakeGraphApiClient(raise_on_add=RuntimeError("Graph 503"))
        result = _node(fake).execute(_resolved_state())

        assert result["status"] == AgentStatus.ERROR.value
        assert "membership_result" not in result
        assert not [ev for ev, _ in emit_calls if ev == "iam.entra.member_add.executed"]


class TestProfileSelection:
    """The fakes are reachable only through the explicit CI opt-in."""

    def test_disabled_without_the_ci_flag(self, monkeypatch):
        monkeypatch.delenv("AGENT_STAGE5_BOUNDED_FAKE", raising=False)
        assert startup_mod.bounded_fake_profile_enabled() is False

    def test_enabled_only_by_the_exact_flag_value(self, monkeypatch):
        monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "true")
        assert startup_mod.bounded_fake_profile_enabled() is False
        monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")
        assert startup_mod.bounded_fake_profile_enabled() is True

    def test_shipped_config_cannot_select_the_fakes(self, monkeypatch):
        """The shipped runtime config carries no tenant, client id or allowlist, so a build
        without the CI flag fails closed instead of quietly falling back to a fake."""
        monkeypatch.delenv("AGENT_STAGE5_BOUNDED_FAKE", raising=False)
        monkeypatch.setattr(startup_mod, "load_runtime_config", lambda: {"max_retry": 3})

        from framework.errors import ConfigError

        with pytest.raises(ConfigError):
            startup_mod.build_agent()

    def test_profile_supplies_everything_through_the_config_channel(self):
        """The graph takes no bespoke constructor keyword: the LLM and the client factory
        travel in the same config dict as the synthetic identifiers."""
        overlay = startup_mod.bounded_fake_profile()

        assert overlay["allowed_groups"] == [FAKE_GROUP_NAME]
        assert isinstance(overlay["llm"], FakeExtractionLLM)
        assert isinstance(overlay["graph_client_factory"]({}), FakeGraphApiClient)

    def test_graph_constructor_takes_config_only(self):
        """The framework prohibits a bespoke `llm=` (or similarly-named) keyword on the outer
        Graph: a caller following the framework contract would never populate it."""
        import inspect

        from src.graph.graph import EntraIDGroupAssignAgentGraph

        params = list(inspect.signature(EntraIDGroupAssignAgentGraph.__init__).parameters)
        assert params == ["self", "config"]


class TestSyntheticValuesAreRecognisable:
    """The synthetic identity must be unusable against a real tenant, and must never be
    mistaken for a credential if it appears in a CI response or trace."""

    def test_email_uses_the_reserved_invalid_tld(self):
        assert FAKE_USER_EMAIL.endswith(".invalid")

    def test_identifiers_are_labelled_not_guid_shaped(self):
        for value in (FAKE_USER_OBJECT_ID, FAKE_GROUP_OBJECT_ID):
            assert value.startswith("stage5-fake-")

    def test_no_secret_material_in_the_audit_payload(self, emit_calls):
        _node(FakeGraphApiClient()).execute(_resolved_state())
        executed = [p for ev, p in emit_calls if ev == "iam.entra.member_add.executed"]

        assert set(executed[0]) == {"user_object_id", "group_id", "add_result", "correlation_id", "ts"}
