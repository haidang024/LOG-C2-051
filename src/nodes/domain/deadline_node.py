"""DeadlineNode — policy-based notice deadline calculation and urgency alerts."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_DEADLINE_RULE_VERSION = "1.1.0"
_TIMEZONE_ASSUMPTION = "UTC"

# Rule table keyed by (mode, class) — days from discovery date
_DEADLINE_RULES: dict[tuple[str, str], dict] = {
    ("sea", "damage"): {
        "notice_window_days": 3,
        "urgent_threshold_days": 1,
        "warning_threshold_days": 2,
        "citation": "Institute Cargo Clauses Notice Provision",
    },
    ("sea", "shortage"): {
        "notice_window_days": 3,
        "urgent_threshold_days": 1,
        "warning_threshold_days": 2,
        "citation": "Hague-Visby Rules Art. III Rule 6",
    },
    ("air", "damage"): {
        "notice_window_days": 14,
        "urgent_threshold_days": 5,
        "warning_threshold_days": 10,
        "citation": "Montreal Convention Art. 31",
    },
    ("air", "shortage"): {
        "notice_window_days": 7,
        "urgent_threshold_days": 3,
        "warning_threshold_days": 5,
        "citation": "Montreal Convention Art. 31",
    },
    ("road", "damage"): {
        "notice_window_days": 7,
        "urgent_threshold_days": 3,
        "warning_threshold_days": 5,
        "citation": "CMR Convention Art. 30",
    },
}
_DEFAULT_NOTICE_WINDOW_DAYS = 14
_DEFAULT_URGENT_DAYS = 5
_DEFAULT_WARNING_DAYS = 10
_DEFAULT_CITATION = "Policy-specific deadline — verify with broker"

_BROKER_VERIFICATION_NOTICE = (
    "Deadline calculation is based on configured policy rules and requires "
    "verification with the broker and insurer. Timezone assumptions: UTC. "
    "Actual contractual deadline may differ."
)


class DeadlineNode(FunctionNode):
    """Calculate notice deadline from policy rules — no LLM inference.

    Inner domain node — trust level ANONYMOUS.
    Missing/conflicting dates or missing rule produce review_required status,
    never a confident deadline.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        incident_class = (state.get("incident_class") or "").lower()
        transport_mode = (state.get("transport_mode") or "").lower()
        discovery_date_str = state.get("discovery_date") or ""
        incident_date_str = state.get("incident_date") or ""
        warnings = list(state.get("workflow_warnings") or [])
        review_conditions = list(state.get("review_conditions") or [])

        # ── Date validation ────────────────────────────────────────────────
        if not discovery_date_str or not incident_date_str:
            review_conditions.append("Missing incident or discovery date — deadline cannot be calculated")
            emit_trace_event(
                "DeadlineNode_missing_dates",
                {"incident_date": incident_date_str, "discovery_date": discovery_date_str},
                state,
            )
            return {
                "deadline_status": "review_required",
                "deadline_rule_version": _DEADLINE_RULE_VERSION,
                "deadline_rule_citation": "N/A — dates missing",
                "workflow_warnings": warnings,
                "review_conditions": review_conditions,
                "status": AgentStatus.SUCCESS.value,
            }

        try:
            discovery_date = datetime.strptime(discovery_date_str, "%Y-%m-%d").date()
            incident_date = datetime.strptime(incident_date_str, "%Y-%m-%d").date()
        except ValueError:
            review_conditions.append("Invalid date format — deadline cannot be calculated")
            return {
                "deadline_status": "review_required",
                "deadline_rule_version": _DEADLINE_RULE_VERSION,
                "deadline_rule_citation": "N/A — invalid date format",
                "workflow_warnings": warnings,
                "review_conditions": review_conditions,
                "status": AgentStatus.SUCCESS.value,
            }

        if discovery_date < incident_date:
            review_conditions.append("Discovery date before incident date — conflicting dates detected")
            return {
                "deadline_status": "review_required",
                "deadline_rule_version": _DEADLINE_RULE_VERSION,
                "deadline_rule_citation": "N/A — conflicting dates",
                "workflow_warnings": warnings,
                "review_conditions": review_conditions,
                "status": AgentStatus.SUCCESS.value,
            }

        # ── Lookup rule ────────────────────────────────────────────────────
        rule = _DEADLINE_RULES.get((transport_mode, incident_class))
        if rule is None:
            # No specific rule — use conservative default and flag for review
            rule = {
                "notice_window_days": _DEFAULT_NOTICE_WINDOW_DAYS,
                "urgent_threshold_days": _DEFAULT_URGENT_DAYS,
                "warning_threshold_days": _DEFAULT_WARNING_DAYS,
                "citation": _DEFAULT_CITATION,
            }
            warnings.append(
                f"No specific deadline rule for mode='{transport_mode}' class='{incident_class}' "
                "— using default 14-day window; broker verification required"
            )

        citation = rule["citation"]
        notice_window = rule["notice_window_days"]
        urgent_threshold = rule["urgent_threshold_days"]
        warning_threshold = rule["warning_threshold_days"]

        deadline_date = discovery_date + timedelta(days=notice_window)
        today = date.today()
        days_remaining = (deadline_date - today).days

        # ── Determine status ──────────────────────────────────────────────
        if days_remaining < 0:
            status = "overdue"
            warnings.append(
                f"Notice deadline is OVERDUE by {abs(days_remaining)} day(s) — urgent broker action required"
            )
            review_conditions.append("Notice deadline has passed — overdue; broker/insurer escalation required")
        elif days_remaining <= urgent_threshold:
            status = "urgent"
            warnings.append(f"Notice deadline is URGENT — {days_remaining} day(s) remaining")
        elif days_remaining <= warning_threshold:
            status = "warning"
            warnings.append(f"Notice deadline WARNING — {days_remaining} day(s) remaining")
        else:
            status = "on_track"

        emit_trace_event(
            "DeadlineNode_calculated",
            {
                "deadline_status": status,
                "deadline_date": deadline_date.isoformat(),
                "days_remaining": days_remaining,
                "rule_citation": citation,
                "rule_version": _DEADLINE_RULE_VERSION,
            },
            state,
        )

        return {
            "deadline_status": status,
            "deadline_date": deadline_date.isoformat(),
            "deadline_days_remaining": days_remaining,
            "deadline_rule_version": _DEADLINE_RULE_VERSION,
            "deadline_rule_citation": citation,
            "deadline_timezone_assumption": _TIMEZONE_ASSUMPTION,
            "workflow_warnings": warnings,
            "review_conditions": review_conditions,
            "status": AgentStatus.SUCCESS.value,
        }
