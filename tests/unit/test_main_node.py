"""LOG-C2-051 — Unit Tests: MainNode (scaffold placeholder — not used in Cat 2 outer graph)."""

# MainNode is the Cat 1 scaffold placeholder. In Cat 2, the `main` slot is
# occupied by CargoClaimGraphNode (GraphNode). MainNode is kept in src/nodes/ to
# satisfy the scaffold file structure; it is not registered in the outer graph.
# These tests verify the scaffold contract only.

from __future__ import annotations

import inspect


class TestMainNode:
    """Unit tests for the scaffold MainNode placeholder."""

    def setup_method(self):
        from src.nodes.main_node import MainNode

        self.node = MainNode()

    def test_success_path(self):
        """TC: Main node processes valid input and returns SUCCESS via __call__."""
        state = {
            "caller_trust_level": "ANONYMOUS",
            "validated_input": "test input",
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)  # use __call__, not execute() directly (CoE R1)
        assert result["status"] == "success"
        assert result["result"] is not None

    def test_empty_input(self):
        """TC: Main node handles empty input gracefully via __call__."""
        state = {
            "caller_trust_level": "ANONYMOUS",
            "validated_input": "",
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)  # use __call__, not execute() directly (CoE R1)
        assert result["status"] == "success"

    def test_execute_method_signature(self):
        """Node contract: Node must implement execute(state) not _invoke_impl."""
        from src.nodes.main_node import MainNode

        assert hasattr(MainNode, "execute"), "MainNode must implement execute()"
        sig = inspect.signature(MainNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2, f"execute() must accept (self, state), got params: {params}"
        assert params[1] == "state", f"Second parameter must be 'state', got '{params[1]}'"
        assert (
            "_invoke_impl" not in MainNode.__dict__
        ), "_invoke_impl() must not be defined in MainNode — use execute() instead"
