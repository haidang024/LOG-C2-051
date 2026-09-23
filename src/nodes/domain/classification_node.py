"""ClassificationNode — deterministic incident classification and evidence-inventory reconciliation."""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_CLASSIFICATION_VERSION = "1.0.0"
_SUPPORTED_MODES = {"sea", "air", "road", "multimodal_standard"}
_SUPPORTED_CLASSES = {"damage", "shortage", "delay", "total_loss", "contamination"}
# Multimodal: only 2-leg combinations are supported
_SUPPORTED_MULTIMODAL_LEGS = {("sea", "road"), ("air", "road")}
# Expected doc types by mode (for photo/reference mismatch checks)
_MODE_EXPECTED_DOC_TYPES = {
    "sea": {"bill_of_lading", "mates_receipt", "survey_report", "outturn_report"},
    "air": {"air_waybill", "arrival_notice"},
    "road": {"cmr_waybill"},
    "multimodal_standard": {"bill_of_lading", "cmr_waybill", "air_waybill"},
}


class ClassificationNode(FunctionNode):
    """Deterministic incident categorization and evidence-inventory reconciliation.

    Inner domain node — trust level ANONYMOUS (trust already verified at PreProcessNode).
    No LLM inference: all classification is rule-based.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        incident_class = (state.get("incident_class") or "").lower()
        transport_mode = (state.get("transport_mode") or "").lower()
        transport_legs = state.get("transport_legs") or []
        evidence_inventory = state.get("evidence_inventory") or []
        warnings = list(state.get("workflow_warnings") or [])
        review_conditions = list(state.get("review_conditions") or [])

        ambiguity_flags: list[str] = []

        # ── Class validation ───────────────────────────────────────────────
        if incident_class not in _SUPPORTED_CLASSES:
            emit_trace_event(
                "ClassificationNode_unsupported_class",
                {"incident_class": incident_class},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"ClassificationNode: unsupported incident_class '{incident_class}'"],
            }

        # ── Mode validation ────────────────────────────────────────────────
        if transport_mode not in _SUPPORTED_MODES:
            emit_trace_event(
                "ClassificationNode_unsupported_mode",
                {"transport_mode": transport_mode},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"ClassificationNode: unsupported transport_mode '{transport_mode}'"],
            }

        # ── Multimodal leg validation ────────────────────────────────────
        if transport_mode == "multimodal_standard" and len(transport_legs) != 2:
            ambiguity_flags.append("multimodal_leg_count_not_2")
            review_conditions.append("Multimodal transport with non-standard leg count requires broker confirmation")

        if transport_mode == "multimodal_standard" and len(transport_legs) == 2:
            leg_modes = tuple(sorted(leg.get("mode", "") for leg in transport_legs))
            if leg_modes not in _SUPPORTED_MULTIMODAL_LEGS:
                ambiguity_flags.append("unsupported_multimodal_combination")
                review_conditions.append(
                    f"Multimodal combination {leg_modes} is not a standard supported combination "
                    "— broker confirmation required"
                )

        # ── Evidence inventory reconciliation ────────────────────────────
        reconciled: list[dict] = []
        for item in evidence_inventory:
            doc_type = item.get("doc_type", "")
            reference = item.get("reference", "")
            verified = item.get("verified", False)
            notes = item.get("notes", "")

            # Photo/reference mismatch: doc_type is "damage_photos" but reference blank
            if doc_type == "damage_photos" and not reference:
                notes = (notes + " [MISMATCH: damage_photos present but no reference ID]").strip()
                ambiguity_flags.append("photo_reference_mismatch")
                warnings.append("Damage photos submitted without a valid reference identifier")

            # Unverified docs remain as-is — never silently treated as verified
            reconciled.append(
                {
                    "doc_type": doc_type,
                    "reference": reference,
                    "status": item.get("status", "unverified") if not verified else "verified",
                    "verified": verified,
                    "notes": notes,
                }
            )

        classification_result = {
            "class": incident_class,
            "mode": transport_mode,
            "legs": transport_legs,
            "ambiguity_flags": ambiguity_flags,
            "version": _CLASSIFICATION_VERSION,
        }

        emit_trace_event(
            "ClassificationNode_classified",
            {
                "incident_class": incident_class,
                "transport_mode": transport_mode,
                "ambiguity_flags": ambiguity_flags,
                "evidence_count": len(reconciled),
                "version": _CLASSIFICATION_VERSION,
            },
            state,
        )

        return {
            "classification_result": classification_result,
            "evidence_inventory": reconciled,
            "workflow_warnings": warnings,
            "review_conditions": review_conditions,
            "status": AgentStatus.SUCCESS.value,
        }
