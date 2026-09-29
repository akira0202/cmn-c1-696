"""PB / S-3 boundary — CMN-C1-696.

ConfirmationFormatNode output must not carry client_secret, raw tokens, or Graph
credentials — neither in the normal output nor if a secret-keyed field is injected.
"""

from src.nodes.confirmation_format_node import ConfirmationFormatNode

_SECRET_MARKERS = ("client_secret", "access_token", "bearer", "token", "password")


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k).lower()
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _state():
    return {
        "membership_result": "added",
        "parsed_user_email": "a@b.co",
        "parsed_group_name": "Finance-Approvers-JP",
        "user_object_id": "U-1",
        "group_object_id": "G-1",
        "business_justification": "role",
        "correlation_id": "pb-s3",
    }


def test_s3_normal_output_has_no_secret_keys():
    node = ConfirmationFormatNode({})
    result = node._extra_security_gate_output(node.execute(_state()))
    keys = set(_keys({"confirmation": result["confirmation"], "audit_record": result["audit_record"]}))
    assert not (keys & set(_SECRET_MARKERS))


def test_s3_extra_gate_redacts_injected_secret():
    scrubbed = ConfirmationFormatNode({})._extra_security_gate_output(
        {"audit_record": {"client_secret": "sk-xxx", "result": "added"}}
    )
    assert scrubbed["audit_record"]["client_secret"] == "[REDACTED]"
    assert scrubbed["audit_record"]["result"] == "added"
