"""The caller-facing output of a compiled invoke.

The node tests pin what ConfirmationFormatNode puts in `formatted_output`; these tests pin what
actually reaches the caller through `invoke()` — the response is read through
`AgentBaseGraph.get_output()`, so a field that is built but not surfaced would pass the node
tests and still be missing here.
"""

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import EntraIDGroupAssignAgentGraph
from src.services.stage5_fakes import (
    FAKE_GROUP_NAME,
    FAKE_GROUP_OBJECT_ID,
    FAKE_JUSTIFICATION,
    FAKE_USER_DISPLAY_NAME,
    FAKE_USER_EMAIL,
    FAKE_USER_OBJECT_ID,
    FakeExtractionLLM,
    FakeGraphApiClient,
)


def _invoke(client, allowed=(FAKE_GROUP_NAME,)):
    graph = EntraIDGroupAssignAgentGraph(
        config={
            "entra_tenant_id": "t",
            "entra_client_id": "c",
            "allowed_groups": list(allowed),
            "require_justification": True,
            "llm": FakeExtractionLLM(),
            "graph_client_factory": lambda state: client,
        }
    )
    graph.compile()
    return graph.invoke(
        "Add the user to the group",
        ctx=InvocationContext(session_id="out", caller_trust_level=TrustLevel.INTERNAL, caller_id="caller"),
    )


def test_success_returns_the_confirmation_with_display_name_and_audit_record():
    out = _invoke(FakeGraphApiClient())

    assert out["status"] == "success"
    body = out["output"]
    assert set(body) == {"result", "user", "group", "effective_at", "business_justification", "audit_record"}
    assert body["result"] == "added"
    assert body["user"] == {
        "email": FAKE_USER_EMAIL,
        "display_name": FAKE_USER_DISPLAY_NAME,
        "object_id": FAKE_USER_OBJECT_ID,
    }
    assert body["group"] == {"display_name": FAKE_GROUP_NAME, "object_id": FAKE_GROUP_OBJECT_ID}
    assert body["business_justification"] == FAKE_JUSTIFICATION

    audit = body["audit_record"]
    assert audit["action"] == "entra_group_member_add"
    assert audit["result"] == "added"
    assert audit["user_object_id"] == FAKE_USER_OBJECT_ID
    assert audit["group_object_id"] == FAKE_GROUP_OBJECT_ID
    assert audit["authorization_basis"] == "ISMS A.9.2.2"
    assert audit["justification_present"] is True
    assert audit["effective_at"] == body["effective_at"]


def test_already_member_is_reported_in_both_the_confirmation_and_the_audit_record():
    body = _invoke(FakeGraphApiClient(add_result="already_member"))["output"]

    assert body["result"] == "already_member"
    assert body["audit_record"]["result"] == "already_member"


def test_missing_display_name_is_returned_as_none():
    body = _invoke(FakeGraphApiClient(user_display_name=None))["output"]

    assert body["user"]["display_name"] is None
    assert body["result"] == "added"


def test_a_refused_request_returns_no_confirmation_or_audit_record():
    client = FakeGraphApiClient(group_display_name="Not-Allowlisted")
    out = _invoke(client, allowed=("Some-Other-Group",))

    assert out["status"] == "error"
    assert out["output"] is None
    assert client.add_member_calls == []


def test_a_failed_write_returns_no_confirmation_or_audit_record():
    out = _invoke(FakeGraphApiClient(raise_on_add=RuntimeError("Graph API 503")))

    assert out["status"] == "error"
    assert out["output"] is None
