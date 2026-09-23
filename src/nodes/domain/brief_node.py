"""BriefNode — grounded claim-brief generation with safe output redaction."""

from __future__ import annotations

import re
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_OUTPUT_BUNDLE_VERSION = "1.0.0"

_MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "**PRELIMINARY ONLY** — This document is a preparatory claim brief for "
    "broker/insurer review. It does not constitute a submitted claim, legal advice, "
    "final coverage determination, or payment entitlement. "
    "All findings must be reviewed and confirmed by a qualified broker and the "
    "insurer before any action is taken."
)

# Redaction patterns applied before output
_FINANCIAL_RE = re.compile(r"(?i)(USD|EUR|JPY|GBP|SGD)\s*[\d,]+(\.\d+)?")
_PII_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Prohibited definitive language — injected text that would claim final coverage
_PROHIBITED_DEFINITIVE_RE = re.compile(
    r"(?i)(coverage is (confirmed|approved|guaranteed|final)|"
    r"claim (accepted|approved|paid|settled)|"
    r"(payment|settlement) (authorized|confirmed))"
)


class BriefNode(FunctionNode):
    """Generate the structured Markdown/JSON preparatory claim brief.

    Inner domain node — trust level ANONYMOUS.
    LLM drafts prose narratives only; all structured fields (status, citations,
    deadlines, gaps) are injected from verified state.
    Applies output redaction to strip PII, financial values, and prohibited language.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        claim_reference = state.get("claim_reference") or "N/A"
        incident_class = state.get("incident_class") or "unknown"
        transport_mode = state.get("transport_mode") or "unknown"
        incident_date = state.get("incident_date") or "N/A"
        discovery_date = state.get("discovery_date") or "N/A"
        incident_narrative = state.get("incident_narrative") or ""
        policy_reference = state.get("policy_reference") or "N/A"
        jurisdiction = state.get("jurisdiction") or "N/A"

        checklist_required = state.get("checklist_required") or []
        checklist_present = state.get("checklist_present") or []
        checklist_gaps = state.get("checklist_gaps") or []
        checklist_version = state.get("checklist_version") or "N/A"

        policy_citations = state.get("policy_citations") or []
        policy_corpus_version = state.get("policy_corpus_version") or "N/A"
        corpus_stale = state.get("policy_corpus_stale") or False

        assessment_posture = state.get("assessment_posture") or "REVIEW_REQUIRED"
        assessment_reasons = state.get("assessment_reasons") or []
        assessment_version = state.get("assessment_version") or "N/A"

        deadline_status = state.get("deadline_status") or "review_required"
        deadline_date = state.get("deadline_date") or "N/A"
        deadline_days_remaining = state.get("deadline_days_remaining")
        deadline_citation = state.get("deadline_rule_citation") or "N/A"

        workflow_warnings = state.get("workflow_warnings") or []
        review_conditions = list(state.get("review_conditions") or [])

        # ── Redact narrative from any residual financial/PII ─────────────
        safe_narrative = _FINANCIAL_RE.sub("[FINANCIAL_VALUE_REDACTED]", incident_narrative)
        safe_narrative = _PII_EMAIL_RE.sub("[EMAIL_REDACTED]", safe_narrative)

        # ── Check for injection: block prohibited definitive language ─────
        if _PROHIBITED_DEFINITIVE_RE.search(safe_narrative):
            safe_narrative = "[NARRATIVE REDACTED — contained prohibited definitive coverage language]"
            workflow_warnings.append(
                "Incident narrative contained prohibited definitive coverage language and was redacted"
            )

        # Determine brief status
        has_critical_gaps = len(checklist_gaps) > 0 and all(g.get("status") == "missing" for g in checklist_gaps)
        if corpus_stale or has_critical_gaps or assessment_posture == "REVIEW_REQUIRED":
            brief_status = "review_blocked"
        else:
            brief_status = "preliminary_not_submitted"

        # ── Build structured Markdown brief ──────────────────────────────
        brief = self._build_brief(
            claim_reference=claim_reference,
            incident_class=incident_class,
            transport_mode=transport_mode,
            incident_date=incident_date,
            discovery_date=discovery_date,
            safe_narrative=safe_narrative,
            policy_reference=policy_reference,
            jurisdiction=jurisdiction,
            checklist_required=checklist_required,
            checklist_present=checklist_present,
            checklist_gaps=checklist_gaps,
            checklist_version=checklist_version,
            policy_citations=policy_citations,
            policy_corpus_version=policy_corpus_version,
            corpus_stale=corpus_stale,
            assessment_posture=assessment_posture,
            assessment_reasons=assessment_reasons,
            assessment_version=assessment_version,
            deadline_status=deadline_status,
            deadline_date=deadline_date,
            deadline_days_remaining=deadline_days_remaining,
            deadline_citation=deadline_citation,
            workflow_warnings=workflow_warnings,
            review_conditions=review_conditions,
            brief_status=brief_status,
        )

        emit_trace_event(
            "BriefNode_brief_produced",
            {
                "brief_status": brief_status,
                "assessment_posture": assessment_posture,
                "deadline_status": deadline_status,
                "checklist_gaps_count": len(checklist_gaps),
                "output_bundle_version": _OUTPUT_BUNDLE_VERSION,
            },
            state,
        )

        return {
            "claim_brief_draft": brief,
            "claim_brief_status": brief_status,
            "output_bundle_version": _OUTPUT_BUNDLE_VERSION,
            "review_conditions": review_conditions,
            "workflow_warnings": workflow_warnings,
            "status": AgentStatus.SUCCESS.value,
        }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _build_brief(
        self,
        claim_reference: str,
        incident_class: str,
        transport_mode: str,
        incident_date: str,
        discovery_date: str,
        safe_narrative: str,
        policy_reference: str,
        jurisdiction: str,
        checklist_required: list,
        checklist_present: list,
        checklist_gaps: list,
        checklist_version: str,
        policy_citations: list,
        policy_corpus_version: str,
        corpus_stale: bool,
        assessment_posture: str,
        assessment_reasons: list,
        assessment_version: str,
        deadline_status: str,
        deadline_date: str,
        deadline_days_remaining: object,
        deadline_citation: str,
        workflow_warnings: list,
        review_conditions: list,
        brief_status: str,
    ) -> str:
        staleness_note = " ⚠️ STALE" if corpus_stale else ""
        days_str = f" ({deadline_days_remaining} days remaining)" if deadline_days_remaining is not None else ""
        gap_lines = (
            "\n".join(
                f"  - [ ] **{g['doc_type']}** — action: {g.get('action', 'N/A')} (owner: {g.get('owner', 'N/A')})"
                for g in checklist_gaps
            )
            or "  *(none)*"
        )
        present_lines = "\n".join(f"  - [x] {d}" for d in checklist_present) or "  *(none verified)*"
        citation_lines = (
            "\n".join(
                f"  - **{c.get('section', 'N/A')}** [{c.get('version', 'N/A')}]: {c.get('excerpt', '')[:120]}..."
                f" *(applicability: {c.get('applicability', 'unknown')})*"
                for c in policy_citations
            )
            or "  *(no citations retrieved)*"
        )
        warning_lines = "\n".join(f"  - ⚠️ {w}" for w in workflow_warnings) or "  *(none)*"
        review_lines = "\n".join(f"  - 🔍 {r}" for r in review_conditions) or "  *(none)*"
        reason_lines = "\n".join(f"  - {r}" for r in assessment_reasons) or "  *(see policy evidence above)*"

        return f"""# Cargo Claim Preparation Brief

**Artifact Status**: `{brief_status}`
**Claim Reference**: {claim_reference}
**Policy Reference**: {policy_reference}
**Jurisdiction**: {jurisdiction}

---

## 1. Sanitized Incident Narrative

- **Incident Class**: {incident_class}
- **Transport Mode**: {transport_mode}
- **Incident Date**: {incident_date}
- **Discovery Date**: {discovery_date}

> {safe_narrative}

---

## 2. Evidence Inventory and Gaps

**Checklist Version**: `{checklist_version}`

### Verified Present
{present_lines}

### Missing / Unverified (Gaps)
{gap_lines}

---

## 3. Preliminary Coverage Position

**Posture**: `{assessment_posture}`
**Assessment Version**: `{assessment_version}`

### Reasons
{reason_lines}

### Policy Citations (Corpus: {policy_corpus_version}{staleness_note})
{citation_lines}

---

## 4. Notice Deadline Alert

- **Deadline Status**: `{deadline_status}`{days_str}
- **Calculated Deadline**: {deadline_date}
- **Governing Clause**: {deadline_citation}

> Deadline requires broker verification. Timezone assumption: UTC.

---

## 5. Next Actions

- Resolve evidence gaps listed in Section 2 before progressing.
- Broker to verify policy citations against current policy schedule.
- Submit formal notice to carrier/insurer if deadline is imminent.
- Do NOT submit this brief as a formal claim without broker/insurer sign-off.

---

## 6. Version Metadata

| Item | Version |
|------|---------|
| Checklist | {checklist_version} |
| Policy Corpus | {policy_corpus_version} |
| Assessment Rules | {assessment_version} |
| Output Template | {_OUTPUT_BUNDLE_VERSION} |

---

## 7. Warnings

{warning_lines}

---

## 8. Review Conditions

{review_lines}
{_MANDATORY_DISCLAIMER}"""
