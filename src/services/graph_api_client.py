"""AgentCore Platform v1.0 — Microsoft Graph API client for CMN-C1-696 EntraIDGroupAssignAgent."""

# Service layer: external API wrapper only. No business logic, no routing.
# Credentials are passed IN by the caller (the node resolves client_secret via
# ctx.secrets.require and hands it here — matching the framework LLM-client
# pattern in shared/services/llm/). This client NEVER touches ctx, os.environ,
# or AgentState. The access token is cached on the instance and never persisted.

from __future__ import annotations

from typing import Any


from urllib.parse import quote

import httpx

from shared.utils.retry_policy import retry_with_backoff

_LOGIN_BASE = "https://login.microsoftonline.com"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"


class GraphApiError(RuntimeError):
    """Non-retryable Microsoft Graph failure (auth / non-404 4xx / exhausted retries)."""


class _RetryableStatus(RuntimeError):
    """Internal signal: HTTP 429 / 5xx — triggers retry_with_backoff."""


class GraphApiClient:
    """Minimal Microsoft Graph client: OAuth2 client-credentials + user/group reads.

    docs/02 §Microsoft Graph API Contract:
      - Resolve user  : GET /v1.0/users/{email}
      - Resolve group : GET /v1.0/groups?$filter=displayName eq '{name}'
      - Auth          : OAuth2 client-credentials (app registration)
      - 429 / 5xx     : exponential backoff (shared.utils.retry_policy — the
                        framework has no ConnectionPolicy; retry_with_backoff is
                        the sole retry mechanism).
    """

    def __init__(
        self,
        tenant_id: str | None,
        client_id: str | None,
        client_secret: str | None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._token: str | None = None

    # -- auth ---------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token is None:
            url = f"{_LOGIN_BASE}/{self._tenant_id}/oauth2/v2.0/token"
            resp = self._send(
                "POST",
                url,
                data={
                    "client_id": self._client_id or "",
                    "client_secret": self._client_secret or "",
                    "scope": _SCOPE,
                    "grant_type": "client_credentials",
                },
            )
            if resp.status_code != 200:
                raise GraphApiError(f"token acquisition failed: HTTP {resp.status_code}")
            token = resp.json().get("access_token")
            if not token:
                raise GraphApiError("token acquisition returned no access_token")
            self._token = token
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    # -- transport (retry on 429 / 5xx) ------------------------------------

    # The decorator is imported from shared, which this project does not type-check.
    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=8.0)  # type: ignore[misc]
    def _send(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        resp = httpx.request(method, url, timeout=self._timeout, **kwargs)  # type: ignore[arg-type]
        if resp.status_code == 429 or resp.status_code >= 500:
            # Retryable — raise so retry_with_backoff applies exponential backoff.
            raise _RetryableStatus(f"retryable HTTP {resp.status_code} from {url}")
        return resp

    # -- reads --------------------------------------------------------------

    def get_user(self, email: str) -> dict[str, Any] | None:
        """GET /users/{email}. Returns the user object, or None when not found (404).

        The email/UPN is URL-encoded into the path (defence-in-depth): guest UPNs
        contain '#' (e.g. ``foo_ext#EXT#@tenant.com``) which must become ``%23``.
        """
        resp = self._send("GET", f"{_GRAPH_BASE}/users/{quote(email, safe='@')}", headers=self._auth_headers())
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GraphApiError(f"get_user failed: HTTP {resp.status_code}")
        user: dict[str, Any] = resp.json()
        return user

    def get_groups_by_display_name(self, display_name: str) -> list[dict[str, Any]]:
        """GET /groups?$filter=displayName eq '{name}'. Returns the match list (0/1/many).

        The display name is embedded in an OData filter; single quotes are
        escaped ('' ) to prevent OData filter injection.
        """
        odata = "displayName eq '" + display_name.replace("'", "''") + "'"
        resp = self._send(
            "GET",
            f"{_GRAPH_BASE}/groups",
            params={"$filter": odata},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise GraphApiError(f"get_groups failed: HTTP {resp.status_code}")
        groups: list[dict[str, Any]] = resp.json().get("value", [])
        return groups

    def add_member(self, group_object_id: str, user_object_id: str) -> str:
        """POST /groups/{id}/members/$ref. Returns 'added', or 'already_member' (idempotent).

        Microsoft Graph returns 204 on success. Adding a user who is already a
        member returns HTTP 400 with an "...already exist..." error message; that
        case is treated as an idempotent success (docs/02 Error#9). Any other
        non-2xx is a GraphApiError (docs/02 Error#11 — no partial write).
        """
        url = f"{_GRAPH_BASE}/groups/{quote(group_object_id, safe='')}/members/$ref"
        body = {"@odata.id": f"{_GRAPH_BASE}/directoryObjects/{user_object_id}"}
        resp = self._send("POST", url, json=body, headers=self._auth_headers())
        if resp.status_code == 204:
            return "added"
        if resp.status_code == 400:
            try:
                message = (resp.json().get("error", {}) or {}).get("message", "")
            except Exception:
                message = resp.text
            # Graph has no distinct error code for already-a-member; the standard
            # signal is this English message substring. If Graph localises/changes
            # the text, this falls through to GraphApiError (safe — surfaced, not
            # silently swallowed) rather than a false already_member.
            if "already exist" in message.lower():
                return "already_member"
            raise GraphApiError(f"add_member failed: HTTP 400: {message}")
        raise GraphApiError(f"add_member failed: HTTP {resp.status_code}")
