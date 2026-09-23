"""PostProcessNode — package the claim brief as a safe, redacted external artifact."""

from __future__ import annotations

import re
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.llm_runtime import provider_metadata, request_advisory

# S-3 content patterns that must not appear in external output
_DEFINITIVE_COVERAGE_RE = re.compile(
    r"(?i)(coverage is (confirmed|approved|guaranteed|final)|"
    r"claim (accepted|approved|paid|settled)|"
    r"(payment|settlement) (authorized|confirmed))"
)
_FINANCIAL_RE = re.compile(r"(?i)(USD|EUR|JPY|GBP|SGD)\s*[\d,]+(\.\d+)?")
_PII_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_DISCLAIMER_MARKER = "PRELIMINARY ONLY"


class PostProcessNode(FunctionNode):
    """Package the claim brief and apply S-3 output boundary checks.

    Outer boundary node (Cat 2) — trust level VERIFIED_EXTERNAL.
    Ensures no definitive/legal coverage statements, no raw financial values,
    and no PII leak into the external claim brief artifact.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, state: dict) -> dict:
        """S-3 extension: reject output if definitive coverage language or financial values remain."""
        # Check the formatted_output bundle first, then fall back to result
        formatted = state.get("formatted_output")
        if isinstance(formatted, dict):
            brief = formatted.get("claim_brief", "")
        else:
            result = state.get("result", {})
            brief = result.get("claim_brief", "") if isinstance(result, dict) else ""
        if not isinstance(brief, str):
            brief = ""

        # Only apply content checks when a non-empty brief has been produced
        if not brief:
            return state

        if _DEFINITIVE_COVERAGE_RE.search(brief):
            from framework.errors import SecurityViolationError

            raise SecurityViolationError("PostProcessNode: output contains prohibited definitive coverage language")
        if _FINANCIAL_RE.search(brief):
            from framework.errors import SecurityViolationError

            raise SecurityViolationError("PostProcessNode: output contains raw financial values")
        if _DISCLAIMER_MARKER not in brief:
            from framework.errors import SecurityViolationError

            raise SecurityViolationError("PostProcessNode: mandatory disclaimer was removed from claim brief")
        return state

    def __init__(self, llm: object | None = None, config: dict | None = None) -> None:
        super().__init__()
        self._llm = llm
        self._config = config or {}

    def execute(self, state: dict) -> dict:
        """Build the external output bundle from inner workflow results."""
        if state.get("input_error_message"):
            message = str(state["input_error_message"])
            return {
                "status": AgentStatus.SUCCESS.value,
                "result": message,
                "formatted_output": message,
                "input_error_message": message,
            }
        request_advisory(
            state,
            "Review the LOG-C2-051 result for clarity, grounding, and safe human review.",
            self._llm,
            timeout_s=float(self._config.get("timeout_s", 30.0)),
            max_retry=int(self._config.get("max_retry", 3)),
        )
        metadata = provider_metadata(state)
        result = state.get("result") or {}
        warnings = state.get("workflow_warnings") or []
        review_conditions = state.get("review_conditions") or []
        brief = state.get("claim_brief_draft") or result.get("claim_brief_draft", "")
        brief_status = state.get("claim_brief_status") or result.get("claim_brief_status", "preliminary_not_submitted")

        # Redact any residual PII from brief text
        if isinstance(brief, str):
            brief = _PII_EMAIL_RE.sub("[EMAIL_REDACTED]", brief)
            brief = _FINANCIAL_RE.sub("[FINANCIAL_VALUE_REDACTED]", brief)

        output_bundle = {
            "artifact_status": brief_status,
            "assessment_posture": state.get("assessment_posture") or result.get("assessment_posture"),
            "deadline_status": state.get("deadline_status") or result.get("deadline_status"),
            "deadline_days_remaining": state.get("deadline_days_remaining") or result.get("deadline_days_remaining"),
            "checklist_gaps_count": len(state.get("checklist_gaps") or result.get("checklist_gaps") or []),
            "review_conditions": review_conditions,
            "workflow_warnings": warnings,
            "claim_brief": brief,
            "checklist_version": state.get("checklist_version") or result.get("checklist_version"),
            "policy_corpus_version": state.get("policy_corpus_version") or result.get("policy_corpus_version"),
            "assessment_version": state.get("assessment_version") or result.get("assessment_version"),
            "output_bundle_version": "1.0.0",
        }

        emit_trace_event(
            "PostProcessNode_bundle_produced",
            {
                "artifact_status": brief_status,
                "assessment_posture": output_bundle.get("assessment_posture"),
                "deadline_status": output_bundle.get("deadline_status"),
                "review_conditions_count": len(review_conditions),
                "warnings_count": len(warnings),
            },
            state,
        )

        return {
            "formatted_output": output_bundle,
            "status": AgentStatus.SUCCESS.value,
            **metadata,
        }
