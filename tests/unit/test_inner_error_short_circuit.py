"""A node failure must not hand control to the next domain node.

Every hop of the workflow is a conditional edge that routes to `finalize` on `status: error`.
Without it a failure still schedules each remaining node in turn: the framework input gate skips a
node whose incoming State is already errored and returns the whole State from
`BaseNode.__call__()`, so `error_log` — an `operator.add` reducer channel — is concatenated onto
itself once per remaining node and the audit trail doubles at each hop.

It matters beyond the audit trail here: a failed user or group resolve must not hand control to
the membership-write node. `finalize` is the terminal, matching where `AgentBaseGraph.route()`
sends a terminal status.

These tests pin the topology itself: every domain hop declares the error branch, the routing
predicate sends an errored State to the terminal node while letting a healthy one advance, and no
hop between two domain nodes is left unconditional.
"""

from framework.schemas.agent_status import AgentStatus

from src.graph.graph import EntraIDGroupAssignAgentGraph


class _EdgeRecorder:
    """Captures what add_edges() declares, without building a LangGraph runtime."""

    def __init__(self):
        self.edges = []
        self.conditional = []

    def add_edge(self, source, target):
        self.edges.append((source, target))

    def add_conditional_edges(self, source, router, mapping):
        self.conditional.append((source, router, mapping))


def _declared():
    graph = EntraIDGroupAssignAgentGraph(config={})
    graph._sg = _EdgeRecorder()
    graph.add_edges()
    return graph._sg


class TestErrorShortCircuitTopology:
    def test_every_domain_hop_declares_an_error_branch(self):
        """All hops but the last are conditional; the last edges to the terminal directly."""
        recorded = _declared()

        assert [source for source, _, _ in recorded.conditional] == list(EntraIDGroupAssignAgentGraph._CHAIN[:-1])

    def test_an_errored_state_routes_to_the_terminal_node(self):
        for _, router, mapping in _declared().conditional:
            branch = router({"status": AgentStatus.ERROR.value})

            assert mapping[branch] == "finalize"

    def test_a_healthy_state_advances_to_the_next_node(self):
        recorded = _declared()

        for (source, router, mapping), nxt in zip(recorded.conditional, list(EntraIDGroupAssignAgentGraph._CHAIN[1:])):
            branch = router({"status": AgentStatus.SUCCESS.value})

            assert mapping[branch] == nxt, f"{source} must advance to {nxt}"

    def test_no_domain_hop_is_left_unconditional(self):
        """An add_edge() between two domain nodes would reintroduce the defect silently."""
        chain = set(EntraIDGroupAssignAgentGraph._CHAIN)

        assert [(s, t) for s, t in _declared().edges if s in chain and t in chain] == []
