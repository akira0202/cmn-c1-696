"""AgentCore Platform v1.0 — InputParseNode for CMN-C1-696 EntraIDGroupAssignAgent."""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state — error_log /
#    node_history carry operator.add reducers, so spreading state double-appends)
#  - Return AgentStatus.<X>.value for status — never the bare enum object
#  - Never import from mediator/, api/, or other agents
#
# Design source of truth: docs/02_design.md
#  - C1 extraction is LLM-based (line 30 "(LLM)"); injected at construction time
#    (framework pattern: framework/nodes/defaults/think_node.py __init__(self, agent, llm)).
#  - C2 Error#1  invalid email  -> raise SecurityViolationError (S-2)
#  - C3 Error#2  justification required but missing -> reject with guidance
#  - C4 Error#3  group ambiguous/unparseable -> error return, NEVER guess

import json
import re
from typing import Any, ClassVar

from framework.errors import ConfigError, SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import explain

# S-2 email/UPN format validation (user@domain.tld). Validation only — the
# extraction itself is performed by the LLM per docs/02 C1.
# Bounds are RFC-derived (local-part 64, label 63, at most 4 labels) so that every
# repetition quantifier is finite; the value validated here is LLM output derived from
# caller free text, where an unbounded quantifier degrades to quadratic backtracking.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,3}\.[A-Za-z]{2,24}$")

# LLM extraction instruction. JSON-only contract; ambiguity must yield null
# (docs/02 C4 — no guessing on an ambiguous group / user).
_EXTRACTION_PROMPT = """You extract structured fields from a natural-language IAM instruction \
that asks to add an existing user to a Microsoft Entra ID (Azure AD) security group.

Return ONLY a single JSON object, no prose, no markdown fences, with exactly these keys:
  "email":         the user's email address or UPN, or null if none is present
  "group_name":    the target group's display name, or null if it is absent or AMBIGUOUS
  "justification": the stated business reason, or null if none is given

Rules:
- Do NOT guess. If the group is ambiguous or not clearly stated, set "group_name" to null.
- Copy values verbatim from the instruction; do not invent, translate, or normalise them.
- Output must be valid JSON parseable by a strict parser.

Instruction:
{instruction}
"""


class InputParseNode(FunctionNode):
    """Parse the NL instruction into user email/UPN, group name, justification.

    - S-1 trust gate: INTERNAL — IAM write executed on behalf of authenticated
      IT admins / upstream systems; anonymous callers are refused.
    - Extraction is LLM-based (docs/02 C1). The LLM client is injected at
      construction time (framework pattern: ThinkNode.__init__(self, agent, llm))
      and called via ``self._llm.complete(prompt, state)`` inside execute(). The parsed
      fields are then validated (email format S-2, justification, group presence)
      before being written to State for the downstream resolve nodes.
    """

    # S-1: privileged IAM-write agent — internal callers only.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def __init__(self, llm: Any = None, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        # LLM client injected by the graph's register_nodes() (docs/02 C1).
        # Defaults to None so the node stays no-arg constructible for PB-6
        # (tests/proof_of_boundary/test_pb_invoke_order.py instantiates every
        # src/nodes/ class via node_cls()); the graph wires the real llm in register_nodes().
        self._llm = llm
        cfg = node_config or {}
        # Enforce a business justification for the audit trail when configured.
        self._require_justification: bool = bool(cfg.get("require_justification", False))

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        emit_progress("Checking the request...")
        user_input = (state.get("user_input") or "").strip()
        corr = state.get("correlation_id")

        if not user_input:
            emit_trace_event(
                "input_parse_rejected",
                {"correlation_id": corr, "reason": "empty_input"},
                state,
            )
            emit_progress(explain("empty_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputParseNode: user_input is empty or missing"],
            }

        # --- C1: LLM extraction --------------------------------------------------
        parsed = self._extract_with_llm(user_input, state)
        if parsed is None:
            emit_trace_event(
                "input_parse_rejected",
                {"correlation_id": corr, "reason": "llm_unparseable"},
                state,
            )
            emit_progress(explain("llm_unparseable"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputParseNode: could not parse a JSON extraction from the LLM response"],
            }

        email = (parsed.get("email") or "").strip() or None
        group = (parsed.get("group_name") or "").strip() or None
        justification = (parsed.get("justification") or "").strip() or None

        # --- C2 (Error#1): a valid user email/UPN is mandatory (S-2) -------------
        if not email or not _EMAIL_RE.match(email):
            emit_trace_event(
                "input_parse_rejected",
                {"correlation_id": corr, "reason": "invalid_email"},
                state,
            )
            # docs/02 Error#1: reject via SecurityViolationError (S-2). BaseNode.
            # __call__ catches it and returns status:error + audit node_error.
            raise SecurityViolationError("InputParseNode: no valid user email/UPN found in instruction (S-2)")

        # --- C4 (Error#3): a resolvable group name is mandatory; no guessing -----
        if not group:
            emit_trace_event(
                "input_parse_rejected",
                {"correlation_id": corr, "reason": "no_group"},
                state,
            )
            emit_progress(explain("no_group"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "InputParseNode: could not extract an unambiguous group name; "
                    "please restate with the exact group display name"
                ],
            }

        # --- C3 (Error#2): enforce the require_justification config ---------------
        if self._require_justification and not justification:
            emit_trace_event(
                "input_parse_rejected",
                {"correlation_id": corr, "reason": "missing_justification"},
                state,
            )
            emit_progress(explain("missing_justification"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "InputParseNode: business_justification is required but was not provided; "
                    "please restate including the business reason for this access"
                ],
            }

        emit_trace_event(
            "input_parsed",
            {"correlation_id": corr, "group": group, "justification_present": bool(justification)},
            state,
        )
        result: dict[str, Any] = {
            "parsed_user_email": email,
            "parsed_group_name": group,
            "status": AgentStatus.SUCCESS.value,
        }
        if justification:
            result["business_justification"] = justification
        return result

    # ------------------------------------------------------------------
    # LLM extraction helpers
    # ------------------------------------------------------------------

    def _extract_with_llm(self, instruction: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """Call the injected LLM and parse its JSON extraction.

        Returns the parsed dict, or None when the response cannot be parsed as
        a JSON object (surfaced as an error return by execute()).
        """
        if self._llm is None:
            # Wiring error: the graph must construct this node with an llm
            # (docs/02 C1). Surfaced as a clear config error, not a NoneType crash.
            raise ConfigError("InputParseNode: llm was not injected at construction")
        prompt = _EXTRACTION_PROMPT.format(instruction=instruction)
        response = self._llm.complete(prompt, state)
        content = response.get("content", "") if isinstance(response, dict) else str(response)
        return self._parse_json_object(content)

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any] | None:
        """Extract the first JSON object from an LLM response, tolerating fences."""
        if not content:
            return None
        text = content.strip()
        # Strip ```json ... ``` / ``` ... ``` fences if present.
        if text.startswith("```"):
            text = re.sub(r"^```[A-Za-z0-9]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # Fall back to the first {...} block in the text.
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
        return obj if isinstance(obj, dict) else None
