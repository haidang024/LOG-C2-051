"""MainNode — scaffold placeholder for Cat 2 (main slot is CargoClaimGraphNode in graph.py)."""

# In this Cat 2 template, the `main` slot in the outer AgentBaseGraph is occupied by
# CargoClaimGraphNode (GraphNode). MainNode is kept in src/nodes/ to satisfy the
# scaffold file structure convention and is NOT registered in graph.py.

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class MainNode(FunctionNode):
    """Cat 2 scaffold placeholder — not registered in outer graph (CargoClaimGraphNode is used instead)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        validated_input = state.get("validated_input", state.get("user_input", ""))

        emit_trace_event(
            "MainNode_placeholder_executed",
            {"validated_input_length": len(validated_input)},
            state,
        )

        return {
            "result": f"[placeholder] received: '{validated_input}'",
            "status": AgentStatus.SUCCESS.value,
        }
