"""PB / S-1 boundary — CMN-C1-696.

allowed_groups allowlist blocks unauthorized group names, and the privileged/admin
group block is NOT bypassable via config (deny precedes the allowlist).
"""

import pytest

from framework.errors import SecurityViolationError
from src.nodes.group_resolve_node import GroupResolveNode


class _FakeClient:
    def get_groups_by_display_name(self, name):
        return [{"id": "G-1"}]  # would resolve if the guards let it through


def _node(allowed):
    n = GroupResolveNode({"allowed_groups": list(allowed)})
    n._graph_client = lambda state: _FakeClient()
    return n


def test_s1_allowlist_blocks_unauthorized_group():
    n = _node(allowed=["Finance-Approvers-JP"])
    with pytest.raises(SecurityViolationError):
        n.execute({"parsed_group_name": "Unauthorized-Team", "correlation_id": "pb-s1"})


def test_s1_privileged_block_not_bypassable_via_config():
    # Even mistakenly listing an admin group in allowed_groups must not grant it.
    n = _node(allowed=["Global Administrator"])
    with pytest.raises(SecurityViolationError):
        n.execute({"parsed_group_name": "Global Administrator", "correlation_id": "pb-s1"})


def test_s1_privileged_block_case_insensitive():
    n = _node(allowed=["User Administrator"])
    with pytest.raises(SecurityViolationError):
        n.execute({"parsed_group_name": "USER ADMINISTRATOR", "correlation_id": "pb-s1"})
