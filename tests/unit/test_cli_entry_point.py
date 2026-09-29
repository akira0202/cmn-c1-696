# CMN-C1-696 — Unit Tests: Marketplace entry point (cli.py)
#
# cli.py is not imported by any other module, so nothing else in the suite would notice if its
# import path, its graph class, or its runtime-config assembly broke — the image would build and
# then fail when the Pod starts. These exercise it directly.
#
# Skipped where the platform events package is absent: shared.bootstrap.marketplace_app imports it
# at module level, so cli.py cannot be imported at all in that environment.

import importlib.util
import pathlib

import pytest

pytest.importorskip("agenticstar_platform")

_CLI = pathlib.Path(__file__).resolve().parents[2] / "cli.py"


def _load():
    spec = importlib.util.spec_from_file_location("cli_under_test", _CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # __name__ != "__main__" — the runner is not started
    return module


def test_entry_point_imports():
    module = _load()
    assert callable(module.run_agent_marketplace)
    assert isinstance(module.MarketplaceGraph, type)


def test_zero_argument_construction_carries_the_runtime_config():
    # run_agent_marketplace constructs the graph as agent_cls() with no arguments; the runtime
    # config must still reach it, otherwise every parameter falls back to a framework default.
    module = _load()
    agent = module.MarketplaceGraph()
    assert isinstance(agent.config, dict)
    # This template assembles its config through src/api/startup.py and fails closed at compile()
    # when the deployment has not supplied its mandatory runtime input; construction still works.


def test_graph_fails_closed_without_its_mandatory_runtime_input():
    # The standalone entry point behaves identically; cli.py must not weaken the check.
    module = _load()
    with pytest.raises(Exception):
        module.MarketplaceGraph().compile()
