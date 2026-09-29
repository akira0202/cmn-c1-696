"""AgentCore Platform v1.0 — EntraIDGroupAssignAgentGraph for CMN-C1-696.

L1-direct AgentBaseGraph. Five FunctionNodes in sequence
(docs/02 Design Decision Record):

    input_parse → user_resolve → group_resolve → member_add → confirmation_format

`add_edges()` is overridden for this non-standard node order — explicitly
permitted by the Graph Builder Contract ("override … only when you
need … non-standard node order").

Slot mapping note: AgentBaseGraph.compile() requires the pre_process / main /
post_process slots to be non-None. The three stage-aligned nodes are therefore
registered under those slot keys — input_parse=pre_process (input handling),
member_add=main (the core write), confirmation_format=post_process (output
formatting) — and the two resolve steps under their own keys. Audit events and
node_history use the node class names, so the slot keys do not affect
observability.
"""

from typing import Any

from langgraph.graph import END, START

from framework.errors import ConfigError
from framework.graph.agent_base_graph import AgentBaseGraph
from framework.schemas.agent_status import AgentStatus
from src.nodes.confirmation_format_node import ConfirmationFormatNode
from src.nodes.group_resolve_node import GroupResolveNode
from src.nodes.input_parse_node import InputParseNode
from src.nodes.member_add_node import MemberAddNode
from src.nodes.user_resolve_node import UserResolveNode
from src.schemas.state import State
from src.services.graph_client_factory import real_graph_client_factory
from src.services.llm import AzureOpenAIExtractionLLM


class EntraIDGroupAssignAgentGraph(AgentBaseGraph):
    """NL → Microsoft Entra ID group-membership assignment with ISMS audit."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        # `BaseGraph.__init__(self, config=None)` is the only constructor the framework
        # defines. Runtime objects — the extraction LLM and the Microsoft
        # Graph client factory — travel through the same config channel, so a caller that
        # follows the framework contract (AgentRegistry passes YAML-derived config) and a
        # caller that can supply objects (src/api/startup.py) use one entry point.
        self._node_config = self.config

    @property
    def name(self) -> str:
        return "EntraIDGroupAssignAgent"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        super()._validate_config()  # framework checks: max_retry, memory_enabled, hitl
        # docs/02 Guardrails: `allowed_groups` is a MANDATORY allowlist. An unset or
        # empty allowlist would deny every assignment — fail fast at startup rather
        # than deploy a silently-deny-all agent (fail-safe, not fail-open).
        if not self.config.get("allowed_groups"):
            raise ConfigError(
                "EntraIDGroupAssignAgent: 'allowed_groups' must be a non-empty allowlist "
                "(mandatory security control — an unset allowlist denies all assignments)."
            )

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize; pre/main/post = None
        cfg = dict(self._node_config or {})
        # Read from config rather than a bespoke constructor keyword. Deployed config is
        # YAML-derived, so `llm` is absent there and the Azure OpenAI adapter is the default;
        # it resolves its connection values per call (docs/02 Production LLM contract).
        llm = cfg.get("llm") or AzureOpenAIExtractionLLM()
        factory = cfg.get("graph_client_factory") or real_graph_client_factory(
            cfg.get("entra_tenant_id"), cfg.get("entra_client_id")
        )
        self._nodes["pre_process"] = InputParseNode(llm, node_config=cfg)
        self._nodes["user_resolve"] = UserResolveNode(node_config=cfg, client_factory=factory)
        self._nodes["group_resolve"] = GroupResolveNode(node_config=cfg, client_factory=factory)
        self._nodes["main"] = MemberAddNode(node_config=cfg, client_factory=factory)
        self._nodes["post_process"] = ConfirmationFormatNode(node_config=cfg)

    # Domain hop order. Each hop is conditional on the running status: a node that returned
    # status:error routes straight to finalize.
    _CHAIN = ("pre_process", "user_resolve", "group_resolve", "main", "post_process")

    def add_edges(self) -> None:
        # Linear 5-node chain (no retry). Graph API 429/5xx retries happen inside
        # GraphApiClient, so no graph-level retry loop is needed. Every hop is conditional:
        # a node returning status:error routes to finalize — the same terminal
        # AgentBaseGraph.route() sends a terminal status to — so the remaining domain nodes are
        # never scheduled. That matters most here: a failed resolve must not hand control to the
        # membership-write node. An unconditional chain would also keep invoking the remaining
        # nodes; each is then skipped by the framework input gate, which returns the whole State,
        # and `error_log` (an operator.add channel) is concatenated onto itself once per remaining
        # node, doubling the audit trail at each hop.
        self._sg.add_edge(START, "initialize")
        self._sg.add_edge("initialize", self._CHAIN[0])
        for node, nxt in zip(self._CHAIN, self._CHAIN[1:]):
            self._sg.add_conditional_edges(
                node,
                self._continue_or_finalize,
                {"continue": nxt, "finalize": "finalize"},
            )
        self._sg.add_edge(self._CHAIN[-1], "finalize")
        self._sg.add_edge("finalize", END)

    def _continue_or_finalize(self, state: dict[str, Any]) -> str:
        """finalize on any node error; otherwise advance to the next domain node."""
        return "finalize" if state.get("status") == AgentStatus.ERROR.value else "continue"
