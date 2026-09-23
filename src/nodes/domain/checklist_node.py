"""ChecklistNode — config-driven evidence checklist generation and gap analysis."""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_CHECKLIST_VERSION = "1.3.0"

# ── Static policy table (mirrors config/config.yaml) ──────────────────────────
_COMMON_REQUIRED = {"commercial_invoice", "packing_list", "transport_contract"}

_MODE_REQUIRED: dict[str, set[str]] = {
    "sea": {"bill_of_lading", "mates_receipt", "survey_report", "outturn_report"},
    "air": {"air_waybill", "arrival_notice"},
    "road": {"cmr_waybill"},
    "multimodal_standard": {"bill_of_lading", "cmr_waybill"},  # conservative union
}

_CLASS_REQUIRED: dict[str, set[str]] = {
    "damage": {"damage_photos", "inspection_certificate"},
    "shortage": {"short_delivery_note"},
    "total_loss": {"total_loss_certificate"},
    "contamination": {"contamination_analysis_report"},
    "delay": set(),
}


class ChecklistNode(FunctionNode):
    """Generate mode × incident-class evidence checklist and identify gaps.

    Inner domain node — trust level ANONYMOUS.
    No LLM inference: checklist generation is fully deterministic from config.
    Missing evidence is never silently treated as present.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        incident_class = (state.get("incident_class") or "").lower()
        transport_mode = (state.get("transport_mode") or "").lower()
        evidence_inventory = state.get("evidence_inventory") or []
        classification_result = state.get("classification_result") or {}
        warnings = list(state.get("workflow_warnings") or [])
        review_conditions = list(state.get("review_conditions") or [])

        ambiguity_flags = classification_result.get("ambiguity_flags", [])
        broker_confirmation_required = bool(review_conditions) or (
            "unsupported_multimodal_combination" in ambiguity_flags
        )

        # ── Build required set ─────────────────────────────────────────────
        required: set[str] = set(_COMMON_REQUIRED)
        required.update(_MODE_REQUIRED.get(transport_mode, set()))
        required.update(_CLASS_REQUIRED.get(incident_class, set()))

        # ── Build verified-present set ─────────────────────────────────────
        verified_doc_types: set[str] = {
            item.get("doc_type", "") for item in evidence_inventory if item.get("verified") is True
        }

        # ── Gap analysis ──────────────────────────────────────────────────
        checklist_required = sorted(required)
        checklist_present = sorted(required & verified_doc_types)
        missing_doc_types = required - verified_doc_types

        checklist_gaps: list[dict] = []
        for doc_type in sorted(missing_doc_types):
            # Determine action owner
            if doc_type in {
                "survey_report",
                "outturn_report",
                "inspection_certificate",
                "total_loss_certificate",
                "contamination_analysis_report",
            }:
                owner = "surveyor"
            elif doc_type in {"bill_of_lading", "air_waybill", "cmr_waybill", "mates_receipt"}:
                owner = "carrier"
            else:
                owner = "claimant"

            checklist_gaps.append(
                {
                    "doc_type": doc_type,
                    "status": "missing",
                    "owner": owner,
                    "action": f"Obtain {doc_type.replace('_', ' ')} from {owner}",
                    "checklist_version": _CHECKLIST_VERSION,
                    "rule_reference": f"checklist-{_CHECKLIST_VERSION}/{transport_mode}/{incident_class}",
                    "broker_confirmation_needed": broker_confirmation_required,
                }
            )

        if checklist_gaps:
            warnings.append(f"{len(checklist_gaps)} required evidence item(s) are missing or unverified")

        emit_trace_event(
            "ChecklistNode_gaps_identified",
            {
                "checklist_version": _CHECKLIST_VERSION,
                "required_count": len(checklist_required),
                "present_count": len(checklist_present),
                "gaps_count": len(checklist_gaps),
                "broker_confirmation_required": broker_confirmation_required,
            },
            state,
        )

        return {
            "checklist_version": _CHECKLIST_VERSION,
            "checklist_required": checklist_required,
            "checklist_present": checklist_present,
            "checklist_gaps": checklist_gaps,
            "broker_confirmation_required": broker_confirmation_required,
            "workflow_warnings": warnings,
            "review_conditions": review_conditions,
            "status": AgentStatus.SUCCESS.value,
        }
