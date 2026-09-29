"""TC-06, TC-07: domain nodes must not override the @final S-2/S-3 gates on FunctionNode."""

from src.nodes.confirmation_format_node import ConfirmationFormatNode
from src.nodes.group_resolve_node import GroupResolveNode
from src.nodes.input_parse_node import InputParseNode
from src.nodes.member_add_node import MemberAddNode
from src.nodes.user_resolve_node import UserResolveNode

DOMAIN_NODES = [
    InputParseNode,
    UserResolveNode,
    GroupResolveNode,
    MemberAddNode,
    ConfirmationFormatNode,
]


def test_final_gates_not_overridden():
    for cls in DOMAIN_NODES:
        assert "_security_gate_input" not in cls.__dict__, f"{cls.__name__} overrides @final _security_gate_input"
        assert "_security_gate_output" not in cls.__dict__, f"{cls.__name__} overrides @final _security_gate_output"


def test_confirmation_extends_only_the_allowed_hook():
    # ConfirmationFormatNode adds S-3 domain logic via the injection hook, not the @final method.
    assert "_extra_security_gate_output" in ConfirmationFormatNode.__dict__
    assert "_security_gate_output" not in ConfirmationFormatNode.__dict__
