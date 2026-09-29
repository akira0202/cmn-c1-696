"""AgentCore Platform v1.0 — Graph API client resolution for CMN-C1-696.

The resolve / add nodes no longer construct a client themselves: the graph builds one
factory and injects it into every node that talks to Microsoft Graph. That keeps the
"which client" decision at a single place, so a deployment profile (real vs the
CI-only bounded fake) is selected once at the startup boundary rather than per node.

The factory is called per invocation with the node's State, because the client secret
is resolved from the request-bound SecretProvider — never at construction, never from
State, never from the process environment.
"""

from typing import Any, Callable, Protocol

from framework.schemas.invocation_context import InvocationContext

from src.services.graph_api_client import GraphApiClient


class GraphClient(Protocol):
    """The surface the nodes rely on. `GraphApiClient` and the CI-only bounded fake
    both satisfy it; nothing else in the template depends on the concrete type."""

    def get_user(self, email: str) -> dict[str, Any] | None: ...

    def get_groups_by_display_name(self, display_name: str) -> list[dict[str, Any]]: ...

    def add_member(self, group_object_id: str, user_object_id: str) -> str: ...


GraphClientFactory = Callable[[dict[str, Any]], GraphClient]


def real_graph_client_factory(tenant_id: str | None, client_id: str | None) -> GraphClientFactory:
    """Return the production factory: a live `GraphApiClient` per invocation.

    The client secret is read from the request-bound SecretProvider inside the returned
    callable, so it is resolved at call time and never held on the node or in State.
    """

    def _factory(state: dict[str, Any]) -> GraphClient:
        ctx = InvocationContext.from_state(state)
        client_secret = ctx.secrets.require("ENTRA_CLIENT_SECRET")
        return GraphApiClient(tenant_id, client_id, client_secret)

    return _factory
