"""AGENTIC STAR Marketplace entry point — one-shot Pod process.

Executed by the Marketplace image's ``CMD ["python", "cli.py"]``.

``run_agent_marketplace`` owns the whole lifecycle: it constructs the graph,
compiles it, builds the SecretProvider, provisions secrets, then runs the
Marketplace loop (input, progress events, terminal delivery, exit). The graph
class is passed uninstantiated.

``agent_name`` / ``namespace`` mirror ``config/agent.yaml`` so the Pod is
identifiable by the same values the manifest and the image tag carry.

``run_agent_marketplace`` constructs the graph as ``agent_cls()`` — with no arguments — so nothing
would reach it and every runtime parameter would silently fall back to its framework default.
The runtime config is assembled exactly as ``src/api/startup.py`` assembles it for the standalone
server, so both entry points build the graph from the same inputs. ``compile()`` still fails closed
when the deployment has not supplied the mandatory allowlist — that is the shipped behaviour and is
not weakened here.
"""

import hashlib
from pathlib import Path
from typing import Any

from shared.bootstrap.marketplace_app import run_agent_marketplace
from shared.utils.log_helper import get_logger
from src.graph.graph import EntraIDGroupAssignAgentGraph
from src.api.startup import bounded_fake_profile, bounded_fake_profile_enabled, load_runtime_config


class MarketplaceGraph(EntraIDGroupAssignAgentGraph):
    """Zero-argument constructor that still assembles the deployment runtime config."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        if config is None:
            config = load_runtime_config()
            if bounded_fake_profile_enabled():
                config.update(bounded_fake_profile())
        super().__init__(config=config)


if __name__ == "__main__":
    # Surface the S-4 audit trail on the Pod's stdout, and name the running build on its first
    # line. The framework already records each domain event — including the exception type when a
    # node fails — but it emits at INFO on the "agentcore.audit" logger, and with no handler
    # attached Python discards everything below WARNING; without this, a failed run leaves nothing
    # in the container log to diagnose from. The build identifier matters because a node only
    # pulls an image tag it does not already hold, so re-publishing under the same tag leaves two
    # builds in service at once — and a log that cannot be attributed to the code that produced it
    # makes a working fix look broken when the run simply used the cached earlier build.
    get_logger("agentcore.audit").info(
        "CMN-C1-696 starting (entry point %s)",
        hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()[:12],
    )

    run_agent_marketplace(
        MarketplaceGraph,
        agent_name="CMN-C1-696",
        namespace="cmn-c1-696",
    )
