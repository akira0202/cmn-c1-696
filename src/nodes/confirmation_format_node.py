"""AgentCore Platform v1.0 — ConfirmationFormatNode for CMN-C1-696 EntraIDGroupAssignAgent."""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus.<X>.value for status — never the bare enum object
#  - Never import from mediator/, api/, or other agents
#
# Design source of truth: docs/02_design.md (node table: confirmation_format;
# S-3 scrub secrets; S-4 event iam.entra.audit_record.emitted; config surface
# group_app_mapping_path / appi_audit_logging).

import json
import logging
from datetime import datetime, timezone
from typing import Any, ClassVar, cast

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import explain

_logger = logging.getLogger("agentcore.cmn_c1_696.confirmation")

# Defence-in-depth S-3 (docs/02): drop any field whose key names a secret before
# returning the confirmation/audit output. This node never inserts secrets, but a
# future edit might; kept narrow so legitimate keys (e.g. authorization_basis) survive.
_SECRET_KEY_HINTS = ("secret", "token", "password", "api_key", "bearer", "credential")


class ConfirmationFormatNode(FunctionNode):
    """Build the structured confirmation and the ISMS A.9.2.2 audit record.

    - S-1: INTERNAL — consistent with the privileged IAM-write pipeline (docs/02
      does not fix a level for this node; INTERNAL matches the others).
    - Produces `confirmation` and `audit_record` (ISMS A.9.2.2, timestamped); both are
      returned to the caller, and the audit event is emitted as well. Optional app-access summary from
      `group_app_mapping_path`; a missing/unparseable file is NON-FATAL — the
      summary is omitted and the node still completes (docs/02 config surface).
    - S-3: never exposes `entra_client_secret` / Graph tokens. The framework's
      @final credential scan runs first; `_extra_security_gate_output` below adds
      a defensive key-name scrub.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        self._group_app_mapping_path = cfg.get("group_app_mapping_path")
        self._appi_audit_logging = bool(cfg.get("appi_audit_logging", True))

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        emit_progress("Preparing the response...")
        corr = state.get("correlation_id")
        membership_result = state.get("membership_result")

        if not membership_result:
            emit_trace_event(
                "confirmation_rejected",
                {"correlation_id": corr, "reason": "missing_membership_result"},
                state,
            )
            emit_progress(explain("missing_membership_result"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ConfirmationFormatNode: membership_result missing (member_add step incomplete)"],
            }

        user_email = state.get("parsed_user_email")
        group_name = state.get("parsed_group_name")
        user_object_id = state.get("user_object_id")
        group_object_id = state.get("group_object_id")
        justification = state.get("business_justification")
        effective_at = datetime.now(timezone.utc).isoformat()

        confirmation: dict[str, Any] = {
            "result": membership_result,  # "added" | "already_member"
            "user": {
                "email": user_email,
                "display_name": state.get("user_display_name"),
                "object_id": user_object_id,
            },
            "group": {"display_name": group_name, "object_id": group_object_id},
            "effective_at": effective_at,
            "business_justification": justification,
        }
        app_summary = self._load_app_access_summary(group_name)
        if app_summary is not None:
            confirmation["app_access_summary"] = app_summary

        audit_record: dict[str, Any] = {
            "action": "entra_group_member_add",
            "result": membership_result,
            "user_object_id": user_object_id,
            "group_object_id": group_object_id,
            "authorization_basis": "ISMS A.9.2.2",
            "justification_present": bool(justification),
            "effective_at": effective_at,
            "correlation_id": corr,
        }

        # S-4: every node must emit >=1 domain event.
        # `appi_audit_logging` gates the ISMS/APPI *audit-record* event specifically:
        #   on  -> emit the docs/02 audit event `iam.entra.audit_record.emitted`;
        #   off -> still emit a generic domain event so the S-4 >=1-event rule holds
        #          (the flag toggles APPI audit emission, not the mandatory S-4 floor).
        # No raw PII / secrets in either payload.
        if self._appi_audit_logging:
            emit_trace_event(
                "iam.entra.audit_record.emitted",
                {
                    "group_id": group_object_id,
                    "authorization_basis": "ISMS A.9.2.2",
                    "justification_present": bool(justification),
                    "correlation_id": corr,
                    "ts": effective_at,
                },
                state,
            )
        else:
            emit_trace_event("confirmation_formatted", {"correlation_id": corr}, state)

        # SC-1: surface the confirmation together with the audit record as formatted_output,
        # so the agent's final output (AgentBaseGraph.get_output → formatted_output or result)
        # returns both to the caller; the default FinalizeNode does not map domain fields.
        # The caller keeps the audit record for its own provisioning evidence. That does not
        # replace the audit event above, which stays the record of the run. The audit record
        # carries identifiers and the authorization basis only — no email, display name or
        # justification text. `confirmation` and `audit_record` are also kept as State fields.
        return {
            "confirmation": confirmation,
            "audit_record": audit_record,
            "formatted_output": {**confirmation, "audit_record": audit_record},
            "status": AgentStatus.SUCCESS.value,
        }

    def _load_app_access_summary(self, group_name: str | None) -> Any:
        """Optional group → app-access summary. Missing/unparseable file is non-fatal."""
        if not self._group_app_mapping_path or not group_name:
            return None
        try:
            with open(self._group_app_mapping_path, encoding="utf-8") as f:
                mapping = json.load(f)
            return mapping.get(group_name)
        except Exception as e:  # non-fatal — omit summary, complete the assignment
            _logger.warning("group_app_mapping load failed (non-fatal): %s", e)
            return None

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 defence-in-depth: redact any secret-keyed field from the output."""
        # _scrub_secret_keys rebuilds a dict for a dict input (it only recurses into dict/list
        # and returns other values unchanged), so a dict in always yields a dict out. The
        # helper is annotated `object -> object` because it walks arbitrary nested values.
        return cast(dict[str, Any], _scrub_secret_keys(result))


def _scrub_secret_keys(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _is_secret_key(k) else _scrub_secret_keys(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_secret_keys(v) for v in obj]
    return obj


def _is_secret_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)
