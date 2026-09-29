"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import os
import secrets
from typing import Any

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.api.startup import bounded_fake_profile_enabled, build_agent

app = FastAPI(title="Agent")

# Startup contract (src/api/startup.py): load config/config.yaml, select the deployment
# profile and inject the LLM + Microsoft Graph client factory, then compile. The shipped
# config carries no tenant, client id or allowlist, so a deployment that supplies none of
# them fails closed here (ConfigError) rather than starting a deny-all IAM writer.
_BOUNDED_FAKE_PROFILE = bounded_fake_profile_enabled()
agent = build_agent()
# Replace namespace/agent_name to match the agent's manifest values.
agent.provision_secrets(secrets_factory(namespace="cmn-c1-696", agent_name="EntraIDGroupAssignAgent"))


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


def _bearer_matches(supplied: str, expected: str) -> bool:
    """Constant-time bearer comparison that is safe for non-ASCII header input."""
    return secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode())


def _resolve_standalone_trust(
    current: TrustLevel,
    authorization: str,
    invoke_auth_token: str | None,
    internal_runner_token: str | None,
) -> TrustLevel:
    """Authenticate standalone callers without allowing external-token elevation.

    STG_INTERNAL_RUNNER_TOKEN is a distinct, CI-generated deployment credential. It is
    considered only for an anonymous caller and maps exactly to INTERNAL;
    INVOKE_AUTH_TOKEN remains VERIFIED_EXTERNAL and can never reach INTERNAL.
    Middleware-established trust is never changed.

    Every node of this agent requires INTERNAL (privileged IAM write), so without this
    resolution a standalone caller is refused at the S-1 gate before any domain node runs.
    """
    if current is not TrustLevel.ANONYMOUS:
        return current
    if internal_runner_token and _bearer_matches(authorization, internal_runner_token):
        return TrustLevel.INTERNAL
    if invoke_auth_token and _bearer_matches(authorization, invoke_auth_token):
        return TrustLevel.VERIFIED_EXTERNAL
    if internal_runner_token or invoke_auth_token:
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")
    return TrustLevel.ANONYMOUS


def _internal_runner_token() -> str | None:
    """The internal runner credential, honoured only under the bounded-fake Stage 5 profile.

    It is the one way a standalone caller reaches INTERNAL, and every node of this agent needs
    INTERNAL to perform the membership write. Under the bounded-fake profile the Graph client
    is an in-process fake, so an INTERNAL caller there changes no real directory. Anywhere
    else the credential is ignored even if it is set, so a leaked or misconfigured runner token
    cannot open a path to a real Entra ID write; a production caller reaches INTERNAL only
    through trust established upstream (`request.state.trust_level`).

    Decided by the profile this process was built with, not re-read per request: a process
    started against the live Graph client never honours the credential, whatever the
    environment says later.
    """
    if not _BOUNDED_FAKE_PROFILE:
        return None
    return os.environ.get("STG_INTERNAL_RUNNER_TOKEN")


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    # This adapter is the entry-point auth boundary (standalone equivalent of the
    # platform AuthMiddleware). Both tokens are deployment-level caller credentials,
    # not agent secrets: no InvocationContext exists before this boundary, so
    # ctx.secrets cannot apply.
    trust = _resolve_standalone_trust(
        getattr(request.state, "trust_level", TrustLevel.ANONYMOUS),
        request.headers.get("authorization", ""),
        os.environ.get("INVOKE_AUTH_TOKEN"),
        _internal_runner_token(),
    )
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return agent.invoke(req.input, ctx=ctx)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "EntraIDGroupAssignAgent"}
