"""AssessmentNode — preliminary coverage assessment with grounded advisory guardrails."""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_ASSESSMENT_VERSION = "1.0.0"

# Posture constants — LLM cannot change these enum values
ELIGIBLE = "ELIGIBLE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
LIKELY_EXCLUDED = "LIKELY_EXCLUDED"

# Exclusion patterns (deterministic, keyword-based — supplemented by citation check)
_EXCLUSION_KEYWORDS = {
    "inherent_vice": ["inherent vice", "natural deterioration", "inherent nature"],
    "delay_only": ["delay"],
    "wilful_misconduct": ["wilful misconduct", "willful misconduct", "deliberate act"],
}

_MANDATORY_DISCLAIMER = (
    "PRELIMINARY ONLY — This preliminary assessment does not constitute a final "
    "coverage determination, legal advice, or payment entitlement. Broker and "
    "insurer confirmation is mandatory before any claim action is taken."
)


class AssessmentNode(FunctionNode):
    """Derive preliminary coverage posture from structured evidence and citations.

    Inner domain node — trust level ANONYMOUS.
    LLM is used for prose narrative only (after evidence is present).
    Assessment enum (ELIGIBLE/REVIEW_REQUIRED/LIKELY_EXCLUDED) is determined
    deterministically before any LLM call — LLM cannot override it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        incident_class = (state.get("incident_class") or "").lower()
        incident_narrative = (state.get("incident_narrative") or "").lower()
        policy_citations = state.get("policy_citations") or []
        retrieval_status = state.get("retrieval_status") or "not_found"
        corpus_stale = state.get("policy_corpus_stale") or False
        checklist_gaps = state.get("checklist_gaps") or []
        review_conditions = list(state.get("review_conditions") or [])
        warnings = list(state.get("workflow_warnings") or [])
        classification_result = state.get("classification_result") or {}
        ambiguity_flags = classification_result.get("ambiguity_flags") or []

        reasons: list[str] = []
        limitations: list[str] = []

        # ── Forced REVIEW_REQUIRED conditions ────────────────────────────
        if corpus_stale:
            review_conditions.append("Corpus is stale — cannot confirm coverage posture")
            posture = REVIEW_REQUIRED
            reasons.append("Policy corpus is stale; posture unconfirmed pending broker review")
            limitations.append("Policy corpus age exceeds freshness threshold")

        elif retrieval_status == "not_found":
            review_conditions.append("No policy citations retrieved — ELIGIBLE posture unsupported")
            posture = REVIEW_REQUIRED
            reasons.append("No policy citations found for this incident class/policy reference combination")
            limitations.append("Policy evidence absent")

        elif checklist_gaps and all(g.get("status") == "missing" for g in checklist_gaps):
            # All required evidence is missing
            review_conditions.append("Critical evidence gaps — assessment cannot proceed to ELIGIBLE")
            posture = REVIEW_REQUIRED
            reasons.append("Required evidence items are missing; claim preparation cannot proceed to ELIGIBLE")
            limitations.append(f"{len(checklist_gaps)} required documents are missing or unverified")

        elif ambiguity_flags:
            review_conditions.append(f"Classification ambiguity: {', '.join(ambiguity_flags)}")
            posture = REVIEW_REQUIRED
            reasons.append(f"Classification ambiguities detected: {', '.join(ambiguity_flags)}")

        else:
            # ── Determine posture from citations ────────────────────────
            posture = self._derive_posture_from_citations(
                incident_class, incident_narrative, policy_citations, reasons, limitations
            )

        # Guard: ELIGIBLE is only valid with citations — no mismatch allowed
        if posture == ELIGIBLE and not policy_citations:
            posture = REVIEW_REQUIRED
            reasons.append("ELIGIBLE posture requires citation support — citation absent")
            limitations.append("No policy citations to support ELIGIBLE determination")

        # ── LLM prose draft (posture is already fixed — LLM cannot override) ─
        # In production: call LLM with structured prompt limiting output to prose.
        # Here we generate a deterministic advisory summary.
        emit_trace_event(
            "AssessmentNode_posture_determined",
            {
                "assessment_posture": posture,
                "assessment_version": _ASSESSMENT_VERSION,
                "citations_count": len(policy_citations),
                "checklist_gaps": len(checklist_gaps),
                "corpus_stale": corpus_stale,
                "reasons_count": len(reasons),
            },
            state,
        )

        return {
            "assessment_posture": posture,
            "assessment_reasons": reasons,
            "assessment_citations": policy_citations,
            "assessment_limitations": limitations,
            "assessment_version": _ASSESSMENT_VERSION,
            "workflow_warnings": warnings,
            "review_conditions": review_conditions,
            "status": AgentStatus.SUCCESS.value,
        }

    # ── Private helpers ────────────────────────────────────────────────────────

    def _derive_posture_from_citations(
        self,
        incident_class: str,
        incident_narrative: str,
        policy_citations: list,
        reasons: list,
        limitations: list,
    ) -> str:
        """Derive posture from citation applicability flags — no LLM inference."""
        exclusion_citations = [c for c in policy_citations if c.get("applicability") == "exclusion"]
        coverage_citations = [c for c in policy_citations if c.get("applicability") == "coverage"]

        # Inherent vice: narrative-level keyword check + ICC-A exclusion citation
        for kws in _EXCLUSION_KEYWORDS.values():
            if any(kw in incident_narrative for kw in kws):
                if exclusion_citations:
                    reasons.append(
                        f"Narrative indicates potential exclusion peril; "
                        f"exclusion clause present: {exclusion_citations[0].get('section', '')}"
                    )
                    return LIKELY_EXCLUDED
                else:
                    reasons.append("Narrative indicates potential exclusion but no citation to confirm")
                    limitations.append("Exclusion keyword found in narrative without supporting citation")
                    return REVIEW_REQUIRED

        # Delay is generally excluded under ICC-A
        if incident_class == "delay" and exclusion_citations:
            reasons.append("Delay is an excluded peril under applicable ICC clauses")
            return LIKELY_EXCLUDED

        if coverage_citations and not exclusion_citations:
            reasons.append("Policy evidence supports coverage for stated incident class and mode")
            return ELIGIBLE

        if exclusion_citations:
            reasons.append("Exclusion clause(s) applicable to the stated peril")
            return LIKELY_EXCLUDED

        # Default to REVIEW_REQUIRED if insufficient evidence
        reasons.append("Insufficient citation evidence to determine posture")
        limitations.append("Policy citations present but no clear coverage/exclusion determination")
        return REVIEW_REQUIRED

    def _build_advisory(self, posture: str, reasons: list, limitations: list) -> str:
        """Build a short advisory prose string — does not alter posture."""
        posture_label = {
            ELIGIBLE: "preliminary ELIGIBLE",
            REVIEW_REQUIRED: "REVIEW REQUIRED",
            LIKELY_EXCLUDED: "LIKELY EXCLUDED",
        }.get(posture, "REVIEW REQUIRED")
        reason_text = "; ".join(reasons) if reasons else "See checklist and policy evidence above."
        return f"Preliminary assessment: {posture_label}. {reason_text} " f"{_MANDATORY_DISCLAIMER}"
