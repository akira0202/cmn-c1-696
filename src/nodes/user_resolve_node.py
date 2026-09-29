"""AgentCore Platform v1.0 — UserResolveNode for CMN-C1-696 EntraIDGroupAssignAgent."""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus.<X>.value for status — never the bare enum object
#  - Never import from mediator/, api/, or other agents
#
# Design source of truth: docs/02_design.md (node table: user_resolve; Error#4).

import unicodedata
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.graph_api_client import GraphApiClient
from src.services.graph_client_factory import GraphClient, GraphClientFactory
from src.services.failure_message import explain
from src.services.progress import emit_progress


class UserResolveNode(FunctionNode):
    """Resolve the parsed user email/UPN to an Entra ID object id via Microsoft Graph.

    - S-1: INTERNAL — part of the privileged IAM-write pipeline (consistent with
      input_parse / member_add).
    - GET /users/{email}; not found → error return (no guessing; the attempt is
      audited via emit_trace_event) — docs/02 Error#4.
    - `client_secret` is resolved via `ctx.secrets.require("ENTRA_CLIENT_SECRET")`
      and passed to the Graph client; never read from os.environ, never written
      to State (S-3). `tenant_id` / `client_id` are non-secret config.
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
        corr = state.get("correlation_id")
        email = state.get("parsed_user_email")

        if not email:
            emit_trace_event(
                "user_resolve_rejected",
                {"correlation_id": corr, "reason": "missing_parsed_user_email"},
                state,
            )
            emit_progress(explain("missing_parsed_user_email"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["UserResolveNode: parsed_user_email is missing"],
            }

        client = self._graph_client(state)
        try:
            user = client.get_user(email)
        except Exception as e:
            emit_trace_event("user_resolve_error", {"correlation_id": corr, "error": str(e)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"UserResolveNode: Graph API error resolving user: {e}"],
            }

        if user is None:
            # docs/02 Error#4 — no assignment, no guessing; the attempt is audited.
            emit_trace_event("user_not_found", {"correlation_id": corr}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"UserResolveNode: user not found: {email}"],
            }

        user_object_id = user.get("id")
        if not user_object_id:
            emit_trace_event("user_resolve_error", {"correlation_id": corr, "error": "no_id"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["UserResolveNode: Graph user object has no id"],
            }

        # Payload carries no raw PII (email, display name) — only correlation (docs/02 S-4 rule).
        emit_trace_event("user_resolved", {"correlation_id": corr}, state)
        return {
            "user_object_id": user_object_id,
            "user_display_name": _display_name(user.get("displayName")),
            "status": AgentStatus.SUCCESS.value,
        }

    def _graph_client(self, state: dict[str, Any]) -> GraphClient:
        # ctx (and its ContextVar-bound secret store) is reconstructed from State;
        # the client_secret never lands in State — it is passed to the client only.
        if self._client_factory is not None:
            return self._client_factory(state)
        ctx = InvocationContext.from_state(state)
        client_secret = ctx.secrets.require("ENTRA_CLIENT_SECRET")
        return GraphApiClient(self._tenant_id, self._client_id, client_secret)


# Entra ID limits displayName to 256 characters.
_DISPLAY_NAME_MAX = 256


def _display_name(value: object) -> str | None:
    """The user's display name for the confirmation, or None.

    Informational only: the assignment is keyed on the object id, so an absent or unusable
    display name never fails the resolve. Only a non-empty string within the Entra length limit
    and free of control characters is kept.
    """
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > _DISPLAY_NAME_MAX:
        return None
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        return None
    return name
