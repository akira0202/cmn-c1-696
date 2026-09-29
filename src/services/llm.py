"""AgentCore Platform v1.0 — extraction LLM resolution for CMN-C1-696.

`input_parse` turns the caller's natural-language instruction into the three structured
fields the resolve nodes need. That step is the template's only LLM call.

The adapter mirrors `graph_client_factory`: the connection values are resolved per
invocation from the request-bound SecretProvider, never at construction, never from State,
never from the process environment. Construction happens while the graph is being built —
before the deployment has provisioned any secret — so an authenticated client cannot exist
at that point, and holding one on the node would also share it across concurrent runs.

The graph falls back to this adapter when `config["llm"]` is absent, the same way it falls
back to the live Graph client factory. A test supplies its own object through `config`.
"""

from typing import Any, Protocol

from framework.schemas.invocation_context import InvocationContext


class ExtractionLLM(Protocol):
    """The surface `InputParseNode` relies on. `AzureOpenAIExtractionLLM` and the CI-only
    bounded fake both satisfy it; nothing else in the template depends on the concrete type.

    Declared for the same reason `GraphClient` is: without a named surface, a stand-in can
    drift from the real client's signature and the divergence only shows up at run time."""

    def complete(self, prompt: str, state: dict[str, Any]) -> dict[str, Any]: ...


# Connection values the deployment binds for the Azure OpenAI endpoint. Declared in
# config/agent.yaml requires.secrets and resolved at complete() time. AZURE_OPENAI_ENDPOINT
# must be the bare resource endpoint: the client rejects a value that already carries an API
# path. AZURE_OPENAI_DEPLOYMENT is the deployment name the endpoint routes on.
_API_KEY = "AZURE_OPENAI_API_KEY"
_ENDPOINT = "AZURE_OPENAI_ENDPOINT"
_DEPLOYMENT = "AZURE_OPENAI_DEPLOYMENT"


def _build_azure_client(config: dict[str, Any]) -> Any:
    """Construct the shared AzureOpenAIClient. Isolated and lazily imported so the provider
    extra is only required on the LLM path, and so tests can substitute it."""
    from shared.services.llm.azure_openai_client import AzureOpenAIClient

    return AzureOpenAIClient(config)


def _extract_text(response: Any) -> str:
    """Pull the completion text out of the shared client's response, tolerating its shapes."""
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, dict):
        return str(response.get("content", "")).strip()
    content = getattr(response, "content", None)
    if content is not None:
        return str(content).strip()
    return str(response).strip()


class AzureOpenAIExtractionLLM:
    """Resolve the connection values per request, call the shared client, return the text.

    The interface is the one `InputParseNode` relies on: ``complete(prompt, state) -> dict``
    carrying the response under ``content``, so the node's JSON parsing is unchanged.
    """

    def __init__(self, temperature: float = 0.0) -> None:
        self._temperature = temperature

    def complete(self, prompt: str, state: dict[str, Any]) -> dict[str, Any]:
        # Resolved at call time from the request-bound provider — NOT at startup.
        secrets = InvocationContext.from_state(state).secrets
        client = _build_azure_client(
            {
                "api_key": secrets.require(_API_KEY),
                "azure_endpoint": secrets.require(_ENDPOINT),
                "azure_deployment": secrets.require(_DEPLOYMENT),
                "temperature": self._temperature,
            }
        )
        return {"content": _extract_text(client.complete([{"role": "user", "content": prompt}]))}
