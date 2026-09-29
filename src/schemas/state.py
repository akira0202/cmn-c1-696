"""AgentCore Platform v1.0 — State schema for CMN-C1-696 EntraIDGroupAssignAgent."""

# ADR-005: State must be a flat TypedDict — never a Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic / dataclass /
# arbitrary Python objects cause silent corruption. Extend AgentState with
# agent-specific fields only.
# NEVER put credentials/secrets (entra_client_secret, Graph API tokens) in
# State — State is checkpointed to the DB, so a secret in State = a secret in
# plaintext in the DB. Graph API credentials are accessed via ctx.secrets.require().

from typing import Any, NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """State for the Entra ID group-membership-assignment pipeline.

    Inherited from AgentState (do NOT redefine here):
      - user_input      : the raw NL instruction
      - validated_input : sanitized input
      - status, session_id, node_history, hitl_*
      - error_log       : error accumulation

    Every agent-specific field below is NotRequired (state-safety):
    each is written by exactly one node and read by later nodes in the sequence
    input_parse -> user_resolve -> group_resolve -> member_add -> confirmation_format.
    """

    # --- written by InputParseNode (parsed from the inherited user_input) ---
    parsed_user_email: NotRequired[str]  # resolved user email / UPN
    parsed_group_name: NotRequired[str]  # requested group display name
    business_justification: NotRequired[str]  # audit justification (when require_justification)

    # --- written by UserResolveNode ---
    user_object_id: NotRequired[str]  # Entra ID user object id
    user_display_name: NotRequired[str | None]  # Entra ID displayName; None when absent or not a usable string

    # --- written by GroupResolveNode (after allowlist + privileged-block guard) ---
    group_object_id: NotRequired[str]  # Entra ID group id

    # --- written by MemberAddNode ---
    membership_result: NotRequired[str]  # "added" | "already_member"

    # --- written by ConfirmationFormatNode ---
    confirmation: NotRequired[dict[str, Any]]  # structured confirmation payload returned to caller
    audit_record: NotRequired[dict[str, Any]]  # ISMS A.9.2.2-compliant timestamped audit record
