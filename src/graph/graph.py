"""Graph — outer Cat 2 AgentBaseGraph for the cargo claim preparation agent (LOG-C2-051)."""

from __future__ import annotations

from typing import Any, ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class CargoClaimGraphNode(GraphNode):
    """Wraps the inner DomainWorkflowGraph; assigned to the `main` slot.

    Bridges outer boundary (PreProcessNode / PostProcessNode) to the multi-step
    cargo-claim domain workflow.
    """

    # Propagate inner errors as SubgraphError to outer graph (fail-fast)
    error_strategy: ClassVar[str] = "propagate"

    # HITL disabled for this template
    propagate_hitl: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any] | None = None, llm: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._config = config or {}
        self._llm = llm

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner cargo-claim domain workflow graph."""
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the canonical intake summary for the inner graph's user_input."""
        return state.get("validated_input", state.get("user_input", ""))

    def execute(self, state: AgentState) -> dict:
        """Override to pass pre-processed outer state fields into the inner graph via input_context."""
        if state.get("input_error_message"):
            return {"status": AgentStatus.SUCCESS.value}
        sub = self.get_subgraph()
        sub.compile()
        user_input = self.extract_input(state)
        ctx = InvocationContext.from_state(state)

        # Carry all pre-processed outer state fields into the inner graph via input_context.
        # BaseGraph.invoke() creates a fresh initial state; without this the inner domain
        # nodes would see empty incident_class, transport_mode, etc.
        inner_input_context = {
            "claim_reference": state.get("claim_reference"),
            "incident_class": state.get("incident_class", ""),
            "transport_mode": state.get("transport_mode", ""),
            "transport_legs": state.get("transport_legs", []),
            "incident_date": state.get("incident_date", ""),
            "discovery_date": state.get("discovery_date", ""),
            "jurisdiction": state.get("jurisdiction", ""),
            "policy_reference": state.get("policy_reference", ""),
            "incident_narrative": state.get("incident_narrative", ""),
            "cargo_value_tier": state.get("cargo_value_tier", "undisclosed"),
            "premium_tier": state.get("premium_tier", "undisclosed"),
            "evidence_inventory": list(state.get("evidence_inventory") or []),
            "workflow_warnings": list(state.get("workflow_warnings") or []),
            "review_conditions": list(state.get("review_conditions") or []),
        }

        sub_result = sub.invoke(
            user_input,
            session_id=ctx.session_id,
            ctx=ctx,
            input_context=inner_input_context,
        )
        return self.merge_output(state, sub_result)

    def merge_output(self, state: AgentState, sub_result: dict) -> dict:
        """Map inner graph sub_result fields back into the outer state.

        Designed together with DomainWorkflowGraph.get_output() to ensure
        field names and types are consistent.
        Returns ONLY the keys this node changes.
        """
        return {
            "result": {
                "claim_brief_draft": sub_result.get("claim_brief_draft"),
                "claim_brief_status": sub_result.get("claim_brief_status"),
                "assessment_posture": sub_result.get("assessment_posture"),
                "deadline_status": sub_result.get("deadline_status"),
                "deadline_days_remaining": sub_result.get("deadline_days_remaining"),
                "checklist_gaps": sub_result.get("checklist_gaps"),
                "checklist_version": sub_result.get("checklist_version"),
                "policy_corpus_version": sub_result.get("policy_corpus_version"),
                "assessment_version": sub_result.get("assessment_version"),
                "workflow_warnings": sub_result.get("workflow_warnings", []),
                "review_conditions": sub_result.get("review_conditions", []),
            },
            # Promote key state fields to outer state for PostProcessNode
            "claim_brief_draft": sub_result.get("claim_brief_draft"),
            "claim_brief_status": sub_result.get("claim_brief_status"),
            "assessment_posture": sub_result.get("assessment_posture"),
            "deadline_status": sub_result.get("deadline_status"),
            "deadline_days_remaining": sub_result.get("deadline_days_remaining"),
            "checklist_gaps": sub_result.get("checklist_gaps"),
            "checklist_version": sub_result.get("checklist_version"),
            "policy_corpus_version": sub_result.get("policy_corpus_version"),
            "assessment_version": sub_result.get("assessment_version"),
            "workflow_warnings": sub_result.get("workflow_warnings", []),
            "review_conditions": sub_result.get("review_conditions", []),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> dict[str, Any]:
        """Forward runtime configuration and the optional LLM to the inner graph."""
        return {**self._config, "llm": self._llm}


class Graph(AgentBaseGraph):
    """Cat 2 outer graph — cargo claim preparation agent (LOG-C2-051).

    Domain logic is encapsulated in CargoClaimGraphNode (`main` slot).
    Backbone: initialize → pre_process → main → post_process → finalize (fixed).

    Output is a preliminary claim brief for broker/insurer review only.
    This agent does NOT submit claims, provide legal advice, or make final
    coverage or payment determinations.
    """

    @property
    def name(self) -> str:
        return "LOG-C2-051"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = CargoClaimGraphNode(
            config=self.config,
            llm=self.config.get("llm"),
        )
        self._nodes["post_process"] = PostProcessNode(
            llm=self.config.get("llm"),
            config=self.config,
        )

    def get_output(self, state: AgentState) -> dict:
        """Extend base output to expose formatted_output as a top-level key.

        AgentBaseGraph.get_output() maps formatted_output → output.
        Callers (tests, server, STG) also access result["formatted_output"] directly,
        so we surface it explicitly alongside the base keys.
        """
        base = super().get_output(state)
        base["generation_mode"] = state.get("generation_mode")
        base["provider_error_message"] = state.get("provider_error_message")
        base["formatted_output"] = state.get("formatted_output")
        context = state.get("input_context")
        is_marketplace = isinstance(context, dict) and "conversation_history" in context
        if not is_marketplace:
            return base
        if _set_marketplace_guidance(base, state, "Cargo claim preparation request"):
            return base
        payload = base.get("output", base.get("formatted_output"))
        if isinstance(payload, dict):
            base["output"] = self._render_marketplace_claim(payload)
        return base

    @staticmethod
    def _render_marketplace_claim(payload: dict[str, Any]) -> str:
        lines = [
            "# Cargo Claim Preparation Brief",
            "",
            f"**Artifact status:** {payload.get('artifact_status', 'unknown')}",
            f"**Assessment posture:** {payload.get('assessment_posture', 'unknown')}",
            f"**Deadline status:** {payload.get('deadline_status', 'unknown')}",
            f"**Days remaining:** {payload.get('deadline_days_remaining', 'unknown')}",
            f"**Checklist gaps:** {payload.get('checklist_gaps_count', 0)}",
        ]
        if payload.get("claim_brief"):
            lines.extend(["", str(payload["claim_brief"])])
        for heading, key in (("Review conditions", "review_conditions"), ("Workflow warnings", "workflow_warnings")):
            items = payload.get(key)
            if isinstance(items, list) and items:
                lines.extend(["", f"{heading}:"])
                lines.extend(f"- {item}" for item in items)
        return "\n".join(lines)

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


def _set_marketplace_guidance(output: dict[str, Any], state: AgentState, subject: str) -> bool:
    context = state.get("input_context")
    message = state.get("input_error_message")
    if not (isinstance(context, dict) and "conversation_history" in context and message):
        return False
    lines = [f"{subject} could not be processed.", "", f"Reason: {message}"]
    guidance = state.get("input_error_guidance")
    if isinstance(guidance, list) and guidance:
        lines.extend(["", "How to continue:"])
        lines.extend(f"- {item}" for item in guidance)
    output["output"] = "\n".join(lines)
    return True
