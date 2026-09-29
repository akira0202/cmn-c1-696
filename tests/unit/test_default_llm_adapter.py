"""The production extraction LLM: the default adapter the graph falls back to.

On the deployed path nothing supplies `config["llm"]` — `config/config.yaml` carries runtime
parameters only — so the graph builds `AzureOpenAIExtractionLLM`. These tests pin its contract
end to end through a compiled `invoke()`:

  * with no `config["llm"]`, the graph uses the Azure OpenAI adapter;
  * the Azure OpenAI connection values are resolved from the request-bound SecretProvider on every
    call and a client is built per call (never cached on the adapter);
  * with a connection value missing, the run fails closed at the first node — no Graph call, no
    membership write, no confirmation.

Only the provider call itself is replaced (`_build_azure_client`); no network is used.
"""

import json

import pytest

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import SecretProvider
from framework.secrets.context import bound_secrets
from src.graph.graph import EntraIDGroupAssignAgentGraph
from src.services import llm as llm_module
from src.services.llm import AzureOpenAIExtractionLLM
from src.services.stage5_fakes import FakeGraphApiClient

GROUP = "Finance-Approvers-JP"
SECRETS = {
    "AZURE_OPENAI_API_KEY": "key-for-test",
    "AZURE_OPENAI_ENDPOINT": "https://example.invalid",
    "AZURE_OPENAI_DEPLOYMENT": "deployment-for-test",
}
INSTRUCTION = "Add Suzuki Hanako (hanako.suzuki@contoso.co.jp) to Finance-Approvers-JP for her new role"


class _Secrets(SecretProvider):
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, default)


class _AzureStandIn:
    def complete(self, messages):
        return {
            "content": json.dumps(
                {"email": "hanako.suzuki@contoso.co.jp", "group_name": GROUP, "justification": "new role"}
            )
        }


@pytest.fixture
def built(monkeypatch):
    """Record every client construction instead of reaching Azure OpenAI."""
    configs: list[dict] = []

    def build(config):
        configs.append(dict(config))
        return _AzureStandIn()

    monkeypatch.setattr(llm_module, "_build_azure_client", build)
    return configs


def _graph(client):
    graph = EntraIDGroupAssignAgentGraph(
        config={
            "entra_tenant_id": "t",
            "entra_client_id": "c",
            "allowed_groups": [GROUP],
            "require_justification": True,
            "graph_client_factory": lambda state: client,
        }
    )
    graph.compile()
    return graph


def _invoke(graph, secrets):
    graph.provision_secrets(_Secrets(secrets))
    ctx = InvocationContext(session_id="llm", caller_trust_level=TrustLevel.INTERNAL, caller_id="caller")
    with bound_secrets(graph._secrets_provider):
        return graph.invoke(INSTRUCTION, ctx=ctx)


def test_without_config_llm_the_graph_uses_the_azure_openai_adapter():
    assert isinstance(_graph(FakeGraphApiClient())._nodes["pre_process"]._llm, AzureOpenAIExtractionLLM)


def test_connection_values_are_resolved_per_call_and_a_client_is_built_per_call(built):
    client = FakeGraphApiClient(group_display_name=GROUP)
    graph = _graph(client)

    first = _invoke(graph, SECRETS)
    second = _invoke(graph, SECRETS)

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert len(built) == 2  # one client per call, not one cached on the adapter
    assert built[0]["api_key"] == SECRETS["AZURE_OPENAI_API_KEY"]
    assert built[0]["azure_endpoint"] == SECRETS["AZURE_OPENAI_ENDPOINT"]
    assert built[0]["azure_deployment"] == SECRETS["AZURE_OPENAI_DEPLOYMENT"]
    assert len(client.add_member_calls) == 2


@pytest.mark.parametrize("missing", sorted(SECRETS))
def test_a_missing_connection_value_fails_closed_before_any_graph_call(built, missing):
    client = FakeGraphApiClient(group_display_name=GROUP)
    secrets = {k: v for k, v in SECRETS.items() if k != missing}

    out = _invoke(_graph(client), secrets)

    assert out["status"] == "error"
    assert out["output"] is None
    assert built == []
    assert client.get_user_calls == []
    assert client.get_groups_calls == []
    assert client.add_member_calls == []
