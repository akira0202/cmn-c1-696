"""TC-05: no node emits node_start / node_complete / node_error inside execute().

The framework's BaseNode.__call__ owns those lifecycle events; templates must not
duplicate them. Enforced by scanning the node sources.
"""

import pathlib
import re

_NODES_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "nodes"
_LIFECYCLE = ("node_start", "node_complete", "node_error")


def test_no_lifecycle_events_emitted_in_nodes():
    offenders = []
    for f in _NODES_DIR.glob("*_node.py"):
        text = f.read_text(encoding="utf-8")
        for ev in _LIFECYCLE:
            if re.search(rf"emit_trace_event\(\s*[\"']{ev}[\"']", text):
                offenders.append(f"{f.name}: {ev}")
    assert not offenders, f"lifecycle events must not be emitted in execute(): {offenders}"
