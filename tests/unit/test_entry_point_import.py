"""Deployment entry-point smoke test.

`src/api/server.py` is what `uvicorn src.api.server:app` loads. Importing it runs every
module-level statement: `build_agent()` reads the runtime config, selects the deployment profile
and compiles, and the secret provider is provisioned. A module-level failure there — an import
that does not resolve against the installed framework, a renamed symbol — means the deployment
does not start, while every unit test that exercises nodes and services in isolation still passes.

The shipped runtime config deliberately omits the deployment-supplied tenant, client id and group
allowlist, so `compile()` fails closed until a deployment provides them. Both directions are
pinned below: the CI profile supplies them and the entry point starts; without it the entry point
refuses to start rather than running with an empty allowlist.
"""

import importlib
import sys

import pytest

from framework.errors import ConfigError


def _fresh_import():
    """Import the entry point without reusing a copy another test already loaded."""
    sys.modules.pop("src.api.server", None)
    return importlib.import_module("src.api.server")


def test_entry_point_module_imports_under_the_ci_profile(monkeypatch):
    monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")

    module = _fresh_import()

    assert module.app is not None
    assert module.agent is not None


def test_entry_point_loads_the_runtime_config(monkeypatch):
    """The graph receives the parsed runtime config, not the empty default a config-less
    construction would leave behind."""
    monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")

    module = _fresh_import()

    assert isinstance(module.agent.config, dict)
    assert module.agent.config


def test_entry_point_fails_closed_without_a_group_allowlist(monkeypatch):
    """`allowed_groups` is a mandatory security control. With the shipped config alone the entry
    point must refuse to start — an unset allowlist must never become an implicit allow."""
    monkeypatch.delenv("AGENT_STAGE5_BOUNDED_FAKE", raising=False)

    with pytest.raises(ConfigError):
        _fresh_import()

    sys.modules.pop("src.api.server", None)
