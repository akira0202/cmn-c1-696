"""AgentCore Platform v1.0 — GroupResolveNode for CMN-C1-696 EntraIDGroupAssignAgent."""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus.<X>.value for status — never the bare enum object
#  - Never import from mediator/, api/, or other agents
#
# Design source of truth: docs/02_design.md (node table: group_resolve;
# Guardrails; Error#5/#6/#7/#8).

from typing import Any, ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.graph_api_client import GraphApiClient
from src.services.graph_client_factory import GraphClient, GraphClientFactory
from src.services.failure_message import explain
from src.services.progress import emit_progress

# Privileged/admin roles denied unconditionally (docs/02 Guardrails, line 130).
# HARD-CODED — not a config key; the deny takes precedence over allowed_groups
# and cannot be disabled by configuration. Matched case-insensitively.
_PRIVILEGED_GROUPS = frozenset(
    {
        "global administrator",
        "privileged role administrator",
        "privileged authentication administrator",
        "security administrator",
        "user administrator",
        "application administrator",
        "conditional access administrator",
    }
)


class GroupResolveNode(FunctionNode):
    """Resolve the parsed group name to an Entra ID group id, under governance gates.

    Order (fail fast — gates run before any Graph API call):
      1. missing group name → error return
      2. privileged/admin group → SecurityViolationError (hard-coded; precedes the
         allowlist so a mistakenly-listed privileged group is still refused — Error#7)
      3. not in allowed_groups allowlist → SecurityViolationError (Error#6)
      4. Graph GET /groups?$filter=displayName eq '{name}' → exactly one match
         required: 0 → not found (Error#5); >1 → ambiguous, reject without
         guessing (Error#8)

    - S-1: INTERNAL. `client_secret` via `ctx.secrets.require("ENTRA_CLIENT_SECRET")`
      (never in State / os.environ). `tenant_id` / `client_id` are non-secret config.
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
        # Fail-safe: an unset allowlist → empty → deny all. The hard "startup fail
        # when allowed_groups is unset" (docs/02 Guardrails) is enforced at graph
        # compile, NOT here — this node must stay no-arg constructible (PB-6).
        self._allowed_groups = set(cfg.get("allowed_groups") or [])

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        group_name = state.get("parsed_group_name")

        if not group_name:
            emit_trace_event(
                "group_resolve_rejected",
                {"correlation_id": corr, "reason": "missing_parsed_group_name"},
                state,
            )
            emit_progress(explain("missing_parsed_group_name"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GroupResolveNode: parsed_group_name is missing"],
            }

        # 2. privileged block — hard-coded, precedes the allowlist (docs/02 Guardrails / Error#7).
        # Matched case-insensitively (strip+lower) so no casing variant escapes the block;
        # the allowlist below is matched exactly (docs/02 "exact match only") — a case/format
        # mismatch there simply fails closed (denied), which is the safe direction.
        if group_name.strip().lower() in _PRIVILEGED_GROUPS:
            emit_trace_event("group_privileged_blocked", {"correlation_id": corr, "group": group_name}, state)
            raise SecurityViolationError(
                f"GroupResolveNode: privileged/admin group is blocked (not overridable): {group_name}"
            )

        # 3. allowlist (Error#6)
        if group_name not in self._allowed_groups:
            emit_trace_event("group_not_allowlisted", {"correlation_id": corr, "group": group_name}, state)
            raise SecurityViolationError(f"GroupResolveNode: group not in allowed_groups: {group_name}")

        # 4. Graph lookup — exactly one match required
        client = self._graph_client(state)
        try:
            groups = client.get_groups_by_display_name(group_name)
        except Exception as e:
            emit_trace_event("group_resolve_error", {"correlation_id": corr, "error": str(e)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"GroupResolveNode: Graph API error resolving group: {e}"],
            }

        if not groups:
            emit_trace_event("group_not_found", {"correlation_id": corr, "group": group_name}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"GroupResolveNode: group not found: {group_name}"],
            }
        if len(groups) > 1:
            # docs/02 Error#8 — ambiguous; reject, never auto-select.
            emit_trace_event(
                "group_ambiguous",
                {"correlation_id": corr, "group": group_name, "count": len(groups)},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"GroupResolveNode: ambiguous group name (>1 match): {group_name}"],
            }

        group_object_id = groups[0].get("id")
        if not group_object_id:
            emit_trace_event("group_resolve_error", {"correlation_id": corr, "error": "no_id"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GroupResolveNode: Graph group object has no id"],
            }

        emit_trace_event("group_resolved", {"correlation_id": corr, "group": group_name}, state)
        return {"group_object_id": group_object_id, "status": AgentStatus.SUCCESS.value}

    def _graph_client(self, state: dict[str, Any]) -> GraphClient:
        if self._client_factory is not None:
            return self._client_factory(state)
        ctx = InvocationContext.from_state(state)
        client_secret = ctx.secrets.require("ENTRA_CLIENT_SECRET")
        return GraphApiClient(self._tenant_id, self._client_id, client_secret)
