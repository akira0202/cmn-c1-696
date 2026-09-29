"""AgentCore Platform v1.0 — MemberAddNode for CMN-C1-696 EntraIDGroupAssignAgent."""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus.<X>.value for status — never the bare enum object
#  - Never import from mediator/, api/, or other agents
#
# Design source of truth: docs/02_design.md (node table: member_add; Error#9/#10/#11;
# S-4 event iam.entra.member_add.executed).

from datetime import datetime, timezone
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.graph_api_client import GraphApiClient
from src.services.graph_client_factory import GraphClient, GraphClientFactory
from src.services.progress import emit_progress
from src.services.failure_message import explain


class MemberAddNode(FunctionNode):
    """Add the resolved user to the resolved group via Microsoft Graph.

    - S-1: INTERNAL — the membership write (docs/02 node table).
    - POST /groups/{id}/members/$ref; already-a-member → idempotent success
      (`already_member`, docs/02 Error#9). Graph 5xx/auth → error return with an
      audited attempt and no partial write (docs/02 Error#11).
    - `client_secret` via `ctx.secrets.require("ENTRA_CLIENT_SECRET")`; never in
      State / os.environ. Writes `membership_result` (state.py; docs/02 calls this
      `add_result`).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def __init__(
        self,
        node_config: dict[str, Any] | None = None,
        client_factory: GraphClientFactory | None = None,
    ) -> None:
        super().__init__()
        cfg = node_config or {}
        # Injected by the graph. None keeps the node no-arg constructible (PB-6)
        # and falls back to building the live client from cfg + the request-bound
        # secret, which is the pre-injection behaviour.
        self._client_factory = client_factory
        self._tenant_id = cfg.get("entra_tenant_id")
        self._client_id = cfg.get("entra_client_id")

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        emit_progress("Resolving the group and the members...")
        corr = state.get("correlation_id")
        user_object_id = state.get("user_object_id")
        group_object_id = state.get("group_object_id")

        if not user_object_id or not group_object_id:
            emit_trace_event(
                "member_add_rejected",
                {"correlation_id": corr, "reason": "missing_resolved_ids"},
                state,
            )
            emit_progress(explain("missing_resolved_ids"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["MemberAddNode: user_object_id / group_object_id missing (resolve step incomplete)"],
            }

        client = self._graph_client(state)
        try:
            result = client.add_member(group_object_id, user_object_id)  # "added" | "already_member"
        except Exception as e:
            # docs/02 Error#11 — audited attempt, no partial write.
            emit_trace_event("member_add_error", {"correlation_id": corr, "error": str(e)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"MemberAddNode: Graph API error adding member: {e}"],
            }

        # S-4 mandatory domain event (docs/02). Payload keys follow the docs/02
        # S-4 contract exactly (user_object_id, group_id, add_result,
        # correlation_id, ts). emit_trace_event also stamps a record-level
        # timestamp/trace_id; no raw PII in the payload.
        emit_trace_event(
            "iam.entra.member_add.executed",
            {
                "user_object_id": user_object_id,
                "group_id": group_object_id,
                "add_result": result,
                "correlation_id": corr,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            state,
        )
        return {"membership_result": result, "status": AgentStatus.SUCCESS.value}

    def _graph_client(self, state: dict[str, Any]) -> GraphClient:
        if self._client_factory is not None:
            return self._client_factory(state)
        ctx = InvocationContext.from_state(state)
        client_secret = ctx.secrets.require("ENTRA_CLIENT_SECRET")
        return GraphApiClient(self._tenant_id, self._client_id, client_secret)
