"""DomainWorkflowGraph — inner cargo-claim preparation multi-step workflow (Cat 2)."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.domain.assessment_node import AssessmentNode
from src.nodes.domain.brief_node import BriefNode
from src.nodes.domain.checklist_node import ChecklistNode
from src.nodes.domain.classification_node import ClassificationNode
from src.nodes.domain.deadline_node import DeadlineNode
from src.nodes.domain.policy_retrieval_node import PolicyRetrievalNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner graph for the cargo claim preparation workflow.

    Inherits BaseGraph for a fully custom node topology.
    Called by CargoClaimGraphNode.get_subgraph() in graph.py.

    Pipeline:
        START → classify → checklist → policy_retrieval → assessment → deadline → brief → END

    Safe terminal routes:
        - Invalid classification → ERROR (early exit from classify node)
        - Critical evidence gaps → brief_status = review_blocked (assessment + brief still run)
        - Stale corpus → REVIEW_REQUIRED posture, brief_status = review_blocked
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "cargo_claim_preparation_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No mandatory config for inner graph — policy tables are module-level constants."""
        pass

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx: Any = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Override to capture input_context before delegating to BaseGraph.invoke().

        CargoClaimGraphNode.execute() passes outer domain fields via input_context.
        We store them so _extra_initial_state() can inject them into the fresh inner state.
        """
        self._outer_fields: dict = input_context or {}
        return super().invoke(user_input, session_id=session_id, ctx=ctx, input_context=input_context)

    def _extra_initial_state(self) -> dict:
        """Inject pre-processed outer state fields into the inner graph's initial state.

        Promotes fields stored by invoke() override to top-level state keys so inner
        domain nodes can read them directly from state.
        """
        ctx = getattr(self, "_outer_fields", {})
        return {
            "claim_reference": ctx.get("claim_reference"),
            "incident_class": ctx.get("incident_class", ""),
            "transport_mode": ctx.get("transport_mode", ""),
            "transport_legs": ctx.get("transport_legs", []),
            "incident_date": ctx.get("incident_date", ""),
            "discovery_date": ctx.get("discovery_date", ""),
            "jurisdiction": ctx.get("jurisdiction", ""),
            "policy_reference": ctx.get("policy_reference", ""),
            "incident_narrative": ctx.get("incident_narrative", ""),
            "cargo_value_tier": ctx.get("cargo_value_tier", "undisclosed"),
            "premium_tier": ctx.get("premium_tier", "undisclosed"),
            "evidence_inventory": list(ctx.get("evidence_inventory") or []),
            "workflow_warnings": list(ctx.get("workflow_warnings") or []),
            "review_conditions": list(ctx.get("review_conditions") or []),
        }

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all cargo-claim domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize (outer backbone concern).
        """
        self._nodes["classify"] = ClassificationNode()
        self._nodes["checklist"] = ChecklistNode()
        self._nodes["policy_retrieval"] = PolicyRetrievalNode()
        self._nodes["assessment"] = AssessmentNode()
        self._nodes["deadline"] = DeadlineNode()
        self._nodes["brief"] = BriefNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the cargo-claim workflow topology with safe error routing."""
        self._sg.add_edge(START, "classify")
        self._sg.add_conditional_edges("classify", self.route_after_classify)
        self._sg.add_edge("checklist", "policy_retrieval")
        self._sg.add_edge("policy_retrieval", "assessment")
        self._sg.add_edge("assessment", "deadline")
        self._sg.add_edge("deadline", "brief")
        self._sg.add_edge("brief", END)

    def route_after_classify(self, state: AgentState) -> str:
        """Route to checklist on success, or END on classification error."""
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "checklist"

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        Primary routing is handled by route_after_classify.
        This method satisfies the ABC requirement for linear fallback.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "checklist"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> dict:
        """Shape the output dict returned to the outer GraphNode as sub_result."""
        return {
            "output": state.get("claim_brief_draft"),
            "claim_brief_draft": state.get("claim_brief_draft"),
            "claim_brief_status": state.get("claim_brief_status"),
            "assessment_posture": state.get("assessment_posture"),
            "deadline_status": state.get("deadline_status"),
            "deadline_days_remaining": state.get("deadline_days_remaining"),
            "checklist_gaps": state.get("checklist_gaps"),
            "checklist_version": state.get("checklist_version"),
            "policy_corpus_version": state.get("policy_corpus_version"),
            "assessment_version": state.get("assessment_version"),
            "workflow_warnings": state.get("workflow_warnings", []),
            "review_conditions": state.get("review_conditions", []),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
