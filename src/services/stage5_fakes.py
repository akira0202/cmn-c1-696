"""AgentCore Platform v1.0 — bounded fakes for the provisional Stage 5 CI profile.

NOT a deployment path. These are selected only by the explicit CI profile resolved in
`src/api/startup.py`; the shipped runtime config cannot reach them, and no production or
AgentRegistry path constructs them.

Why they exist: the provisional Stage 5 job boots the template inside a CI container and
performs one first invoke. This agent's core step is a privileged Microsoft Entra ID
membership write, so a live first invoke would change real directory permissions as a side
effect of a pipeline run. The bounded fakes keep the full workflow — parse, resolve user,
resolve group, allowlist check, membership write, confirmation, audit — executing exactly as
in production, and replace only the two external boundaries: the LLM call and the Graph API
call. What Stage 5 then proves is the deploy -> invoke -> workflow contract, NOT that a real
Entra write succeeded. See docs/07 for that distinction.

Every synthetic value below is deliberately unusable against a real tenant: the address uses
the reserved `.invalid` TLD (RFC 2606), and the identifiers are readable strings rather than
GUID-shaped values, so they cannot be mistaken for production identifiers if they appear in a
CI response.
"""

import json
from typing import Any

# Synthetic identity used by the CI profile. `.invalid` is reserved and can never resolve.
FAKE_USER_EMAIL = "stage5-bounded-fake-user@ci.invalid"
FAKE_GROUP_NAME = "CI-Stage5-Bounded-Fake-Group"
FAKE_JUSTIFICATION = "Provisional Stage 5 bounded-fake verification (CI profile only)."
FAKE_USER_OBJECT_ID = "stage5-fake-user-object-id"
FAKE_USER_DISPLAY_NAME = "Stage5 Bounded Fake User"
FAKE_GROUP_OBJECT_ID = "stage5-fake-group-object-id"


class FakeExtractionLLM:
    """Minimal `complete(prompt, state) -> dict` stub for `InputParseNode` (the `ExtractionLLM` surface).

    Returns the extraction JSON the real provider would return. The node's own parsing,
    email-format validation, group-presence check and justification guard all run for real
    against this output — only the provider call is replaced.
    """

    def __init__(
        self,
        email: str | None = FAKE_USER_EMAIL,
        group_name: str | None = FAKE_GROUP_NAME,
        justification: str | None = FAKE_JUSTIFICATION,
        raw_content: str | None = None,
    ) -> None:
        # `raw_content` lets a unit test drive the malformed-response path without
        # constructing a provider double of its own.
        self._raw_content = raw_content
        self._payload = {
            "email": email,
            "group_name": group_name,
            "justification": justification,
        }
        self.calls: list[str] = []

    def complete(self, prompt: str, state: dict[str, Any]) -> dict[str, Any]:
        # `state` is part of the ExtractionLLM surface (the real adapter resolves its
        # secrets from it). The fake needs no secret, but the parameter must be accepted:
        # InputParseNode calls complete(prompt, state) positionally.
        self.calls.append(prompt)
        content = self._raw_content if self._raw_content is not None else json.dumps(self._payload)
        return {"content": content}


class FakeGraphApiClient:
    """In-process stand-in for `GraphApiClient` — resolves and "writes" nothing external.

    Records every call so a test can assert that the write actually happened and that the
    resolved identifiers were the ones passed down the graph. `add_member` returns the same
    two contract values as the real client (`added` / `already_member`), and can be made to
    raise so the failure path is exercised without a network.
    """

    def __init__(
        self,
        *,
        add_result: str = "added",
        raise_on_add: Exception | None = None,
        user_object_id: str = FAKE_USER_OBJECT_ID,
        user_display_name: str | None = FAKE_USER_DISPLAY_NAME,
        group_object_id: str = FAKE_GROUP_OBJECT_ID,
        group_display_name: str = FAKE_GROUP_NAME,
    ) -> None:
        self._add_result = add_result
        self._raise_on_add = raise_on_add
        self._user_object_id = user_object_id
        self._user_display_name = user_display_name
        self._group_object_id = group_object_id
        self._group_display_name = group_display_name
        self.get_user_calls: list[str] = []
        self.get_groups_calls: list[str] = []
        self.add_member_calls: list[tuple[str, str]] = []

    def get_user(self, email: str) -> dict[str, Any] | None:
        self.get_user_calls.append(email)
        return {"id": self._user_object_id, "userPrincipalName": email, "displayName": self._user_display_name}

    def get_groups_by_display_name(self, display_name: str) -> list[dict[str, Any]]:
        self.get_groups_calls.append(display_name)
        if display_name != self._group_display_name:
            return []
        return [{"id": self._group_object_id, "displayName": self._group_display_name}]

    def add_member(self, group_object_id: str, user_object_id: str) -> str:
        self.add_member_calls.append((group_object_id, user_object_id))
        if self._raise_on_add is not None:
            raise self._raise_on_add
        return self._add_result
