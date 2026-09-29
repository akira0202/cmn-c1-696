"""AgentCore Platform v1.0"""

# AgentRegistry loads the graph via `module: src.graph` + `class: ...` (agent.yaml).
from src.graph.graph import EntraIDGroupAssignAgentGraph

__all__ = ["EntraIDGroupAssignAgentGraph"]
