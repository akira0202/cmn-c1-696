"""AgentCore Platform v1.0 — deployment startup contract for CMN-C1-696.

Single place that turns a deployment into a running agent:

  - load the runtime config (`config/config.yaml`) and pass it to the graph;
  - inject the LLM and the Microsoft Graph client factory;
  - select the deployment profile.

The graph and its nodes read only `self.config` and what is injected into them. Profile
selection — the one thing that legitimately depends on where the process is running — lives
here, at the startup boundary, so the agent's business and authorization logic never reads
the process environment.

This module has NO import-time side effects (no agent is constructed on import), so tests can
exercise every branch without triggering the deployment construction in `server.py`.
"""

import os
import pathlib
from typing import Any

from framework.utils.config_loader import load_config

from src.graph.graph import EntraIDGroupAssignAgentGraph

_CONFIG_YAML = pathlib.Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# The provisional Stage 5 CI profile. One flag selects BOTH bounded fakes together, so a
# half-faked run — real Graph writes driven by a fake LLM, or the reverse — is not reachable.
# It is set only by the rehearsal job and never on a real environment.
_STAGE5_BOUNDED_FAKE_ENV = "AGENT_STAGE5_BOUNDED_FAKE"


def load_runtime_config() -> dict[str, Any]:
    """Load `config/config.yaml` — the runtime parameters passed as Graph(config=...).

    The static AgentRegistry manifest (`config/agent.yaml`) holds only discovery/registry
    keys. An absent file yields {}, which leaves the mandatory `allowed_groups` unset and
    therefore fails closed at compile.
    """
    if not _CONFIG_YAML.exists():
        return {}
    return dict(load_config(str(_CONFIG_YAML)))


def bounded_fake_profile_enabled() -> bool:
    """True only under the explicit CI opt-in. Nothing in `config/config.yaml` can turn
    the fakes on: the shipped config carries no tenant, client id or allowlist at all."""
    return os.environ.get(_STAGE5_BOUNDED_FAKE_ENV) == "1"


def bounded_fake_profile() -> dict[str, Any]:
    """Return the config overlay for the provisional Stage 5 profile.

    Everything the profile needs travels in the config dict the graph already receives: the
    fake extraction LLM under `llm`, the fake client factory under `graph_client_factory`, and
    the synthetic identifiers. The graph therefore needs no bespoke constructor keyword — it
    reads the same channel whether AgentRegistry supplied YAML-derived config or this module
    supplied objects.

    The overlay also supplies the single allowlisted synthetic group, so the real
    `_validate_config()` allowlist guard runs against a non-empty list exactly as it would in a
    deployment. Imported lazily so the CI-only fakes are not loaded on a production start.
    """
    from src.services.stage5_fakes import (
        FAKE_GROUP_NAME,
        FakeExtractionLLM,
        FakeGraphApiClient,
    )

    fake_client = FakeGraphApiClient()
    return {
        "entra_tenant_id": "stage5-fake-tenant-id",
        "entra_client_id": "stage5-fake-client-id",
        "allowed_groups": [FAKE_GROUP_NAME],
        "require_justification": True,
        "llm": FakeExtractionLLM(),
        "graph_client_factory": lambda state: fake_client,
    }


def build_agent(config_overrides: dict[str, Any] | None = None) -> EntraIDGroupAssignAgentGraph:
    """Build and compile the agent from the runtime config plus any injected objects.

    With no bounded-fake profile and no overrides, the graph builds the live `GraphApiClient`
    and `compile()` fails closed when `allowed_groups` is unset — the shipped behaviour. Unit
    tests pass their doubles through `config_overrides` and never set the CI flag.
    """
    cfg = load_runtime_config()
    if bounded_fake_profile_enabled():
        cfg.update(bounded_fake_profile())
    if config_overrides:
        cfg.update(config_overrides)

    agent = EntraIDGroupAssignAgentGraph(config=cfg)
    agent.compile()  # _validate_config() runs here — fail-closed on an unset allowlist
    return agent
