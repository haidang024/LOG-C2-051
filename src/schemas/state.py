"""State — flat TypedDict for the cargo claim preparation agent (LOG-C2-051)."""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# alternatives). LangGraph checkpoints use msgpack serialization, so only
# plain serializable fields are allowed. Do NOT add credentials or secrets.
# Structured payloads (list/dict) are JSON-encoded as str for msgpack safety.

from __future__ import annotations

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class State(AgentState):
    """Cargo claim preparation agent state.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    Domain fields below capture the fixed workflow:
      intake → classification → checklist/gaps → policy retrieval →
      assessment → deadline alert → claim brief → output
    """

    # ── Intake / Canonical incident facts ─────────────────────────────────────
    claim_reference: Optional[str]  # Client-assigned claim reference (opaque ID)
    incident_class: Optional[str]  # damage|shortage|delay|total_loss|contamination
    transport_mode: Optional[str]  # sea|air|road|multimodal_standard
    # JSON-encoded list[dict]: [{"mode": "sea", "leg": 1}, ...]
    transport_legs: Optional[str]
    incident_date: Optional[str]  # ISO-8601 date string
    discovery_date: Optional[str]  # ISO-8601 date string
    jurisdiction: Optional[str]  # Applicable law jurisdiction code
    policy_reference: Optional[str]  # Policy schedule ID (opaque)
    incident_narrative: Optional[str]  # Sanitized (PII-stripped) incident description
    # Cargo value tier (no raw financial value): "low"|"medium"|"high"|"undisclosed"
    cargo_value_tier: Optional[str]
    # Premium tier: "standard"|"enhanced"|"undisclosed" (raw premium never stored)
    premium_tier: Optional[str]
    validated_input: Optional[str]  # Canonical incident summary after intake

    # ── Evidence inventory ──────────────────────────────────────────────────────
    # JSON-encoded list[dict]: each item has
    # {"doc_type": str, "reference": str, "status": str, "verified": bool, "notes": str}
    evidence_inventory: Optional[str]

    # ── Classification results ─────────────────────────────────────────────────
    # JSON-encoded dict: {"class": str, "mode": str,
    #                     "ambiguity_flags": list, "version": str}
    classification_result: Optional[str]

    # ── Evidence checklist and gaps ────────────────────────────────────────────
    checklist_version: Optional[str]  # Checklist rule version applied
    # JSON-encoded list: required items per policy
    checklist_required: Optional[str]
    # JSON-encoded list: items present and verified
    checklist_present: Optional[str]
    # JSON-encoded list: missing/unverified items with owner/action
    checklist_gaps: Optional[str]
    broker_confirmation_required: Optional[bool]  # True if complex/ambiguous case

    # ── Policy evidence retrieval ──────────────────────────────────────────────
    policy_corpus_version: Optional[str]  # Corpus version used
    policy_corpus_freshness_days: Optional[int]  # Days since last corpus update
    policy_corpus_stale: Optional[bool]  # True if > staleness_threshold_days
    # JSON-encoded list[dict]:
    # {"source": str, "section": str, "version": str, "effective_date": str,
    #  "excerpt": str, "applicability": str}
    policy_citations: Optional[str]
    retrieval_status: Optional[str]  # "found"|"not_found"|"stale"|"mismatch"

    # ── Preliminary coverage assessment ────────────────────────────────────────
    assessment_posture: Optional[str]  # ELIGIBLE|REVIEW_REQUIRED|LIKELY_EXCLUDED
    # JSON-encoded list[str]: reasons
    assessment_reasons: Optional[str]
    # JSON-encoded list: citations backing the posture
    assessment_citations: Optional[str]
    # JSON-encoded list: evidence or data limitations
    assessment_limitations: Optional[str]
    assessment_version: Optional[str]  # Assessment rule/prompt version

    # ── Deadline alert ─────────────────────────────────────────────────────────
    deadline_status: Optional[str]  # on_track|warning|urgent|overdue|review_required
    deadline_date: Optional[str]  # ISO-8601 calculated deadline
    deadline_days_remaining: Optional[int]  # Days remaining (negative = overdue)
    deadline_rule_version: Optional[str]  # Rule version applied
    deadline_rule_citation: Optional[str]  # Clause/article reference
    deadline_timezone_assumption: Optional[str]  # Timezone used in calculation

    # ── Claim brief ────────────────────────────────────────────────────────────
    claim_brief_draft: Optional[str]  # Structured Markdown claim brief
    claim_brief_status: Optional[str]  # "preliminary_not_submitted"|"review_blocked"
    output_bundle_version: Optional[str]  # Template version used for output

    # ── Audit / trace ──────────────────────────────────────────────────────────
    # JSON-encoded list: non-fatal warnings accumulated across nodes
    workflow_warnings: Optional[str]
    # JSON-encoded list: active conditions requiring broker/insurer review
    review_conditions: Optional[str]
    input_error_message: str | None
    input_error_guidance: list[str]
    generation_mode: str | None
    provider_error_message: str | None
