"""Unit tests for ConfirmationFormatNode — CMN-C1-696. Covers BL-01, BL-14, SEC-S3-a, TC-10."""

import pytest

from framework.schemas.agent_status import AgentStatus
from src.nodes import confirmation_format_node as mod
from src.nodes.confirmation_format_node import ConfirmationFormatNode


@pytest.fixture
def emit_spy(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(mod, "emit_trace_event", lambda ev, p, s: calls.append(ev))
    return calls


def _state() -> dict:
    return {
        "membership_result": "added",
        "parsed_user_email": "a@b.co",
        "parsed_group_name": "Finance-Approvers-JP",
        "user_object_id": "U-1",
        "group_object_id": "G-1",
        "business_justification": "new role",
        "correlation_id": "x",
    }


def test_success_builds_confirmation_and_audit(emit_spy):  # BL-01 (happy path, node-level)
    r = ConfirmationFormatNode({}).execute(_state())
    assert r["status"] == AgentStatus.SUCCESS.value
    assert not isinstance(r["status"], AgentStatus)
    assert r["confirmation"]["result"] == "added"
    assert r["audit_record"]["authorization_basis"] == "ISMS A.9.2.2"
    assert r["formatted_output"] == {**r["confirmation"], "audit_record": r["audit_record"]}  # SC-1
    assert "iam.entra.audit_record.emitted" in emit_spy


def test_missing_membership_result_errors(emit_spy):
    r = ConfirmationFormatNode({}).execute({"correlation_id": "x"})
    assert r["status"] == AgentStatus.ERROR.value
    assert not isinstance(r["status"], AgentStatus)


def test_s3_scrub_redacts_secret_keys():  # TC-10 / SEC-S3-a
    scrubbed = ConfirmationFormatNode({})._extra_security_gate_output(
        {"confirmation": {"result": "added", "access_token": "SECRET", "authorization_basis": "ISMS A.9.2.2"}}
    )
    assert scrubbed["confirmation"]["access_token"] == "[REDACTED]"
    assert scrubbed["confirmation"]["authorization_basis"] == "ISMS A.9.2.2"  # not over-scrubbed


def test_appi_off_still_emits_domain_event(emit_spy):
    r = ConfirmationFormatNode({"appi_audit_logging": False}).execute(_state())
    assert r["status"] == AgentStatus.SUCCESS.value
    assert not isinstance(r["status"], AgentStatus)
    assert "confirmation_formatted" in emit_spy  # S-4 floor still met


def test_confirmation_carries_the_resolved_display_name(emit_spy):
    r = ConfirmationFormatNode({}).execute({**_state(), "user_display_name": "Suzuki Hanako"})
    assert r["confirmation"]["user"] == {"email": "a@b.co", "display_name": "Suzuki Hanako", "object_id": "U-1"}


def test_confirmation_display_name_is_none_when_not_resolved(emit_spy):
    r = ConfirmationFormatNode({}).execute(_state())
    assert r["confirmation"]["user"]["display_name"] is None


def test_audit_record_is_returned_to_the_caller_and_still_emitted(emit_spy):
    r = ConfirmationFormatNode({}).execute(_state())
    assert r["formatted_output"]["audit_record"] == r["audit_record"]
    assert "iam.entra.audit_record.emitted" in emit_spy  # the returned copy does not replace the event


def test_returned_audit_record_carries_identifiers_and_basis_only(emit_spy):
    """No email, display name or justification text in the audit record handed to the caller."""
    r = ConfirmationFormatNode({}).execute({**_state(), "user_display_name": "Suzuki Hanako"})
    audit = r["formatted_output"]["audit_record"]
    assert set(audit) == {
        "action",
        "result",
        "user_object_id",
        "group_object_id",
        "authorization_basis",
        "justification_present",
        "effective_at",
        "correlation_id",
    }
    flat = repr(audit)
    assert "a@b.co" not in flat
    assert "Suzuki Hanako" not in flat
    assert "new role" not in flat
    assert audit["justification_present"] is True


def test_s3_scrub_reaches_the_returned_audit_record():
    scrubbed = ConfirmationFormatNode({})._extra_security_gate_output(
        {"formatted_output": {"result": "added", "audit_record": {"client_secret": "SECRET", "result": "added"}}}
    )
    assert scrubbed["formatted_output"]["audit_record"]["client_secret"] == "[REDACTED]"
    assert scrubbed["formatted_output"]["audit_record"]["result"] == "added"
