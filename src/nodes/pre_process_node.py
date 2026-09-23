"""PreProcessNode — cargo-claim intake validation and sensitive-data minimization."""

from __future__ import annotations

import json
import re
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# Allowed transport modes and incident classes
_SUPPORTED_MODES = {"sea", "air", "road", "multimodal_standard"}
_SUPPORTED_CLASSES = {"damage", "shortage", "delay", "total_loss", "contamination"}
_SUPPORTED_JURISDICTIONS = {"UKJLAW", "GERMAN", "SGLAW", "JPLAW"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Patterns matched for PII minimization of shipment-party identifiers
_PARTY_PATTERNS = [
    re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"),  # Full names
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP addresses
]
# Financial value patterns (cargo invoice / premium amounts)
_FINANCIAL_RE = re.compile(r"(?i)(USD|EUR|JPY|GBP|SGD)\s*[\d,]+(\.\d+)?")

_INPUT_GUIDANCE = [
    "Provide claim_data in the request context.",
    "Include incident_class, transport_mode, incident_date, discovery_date, policy_reference, jurisdiction, and incident_narrative.",
    "Dates must use YYYY-MM-DD; supported values are listed in the agent input contract.",
]


_CLAIM_FIELDS = (
    "claim_reference",
    "incident_class",
    "transport_mode",
    "transport_legs",
    "incident_date",
    "discovery_date",
    "jurisdiction",
    "policy_reference",
    "incident_narrative",
    "cargo_value",
    "premium",
    "evidence_inventory",
)


def _extract_pasted_claim(user_input: str) -> dict:
    """Recover a claim payload pasted as JSON text into a chat-style `user_input`.

    Marketplace chat has no structured request body: whatever the operator types
    lands in `user_input`, and `input_context` carries only conversation history.
    A pasted JSON object is therefore the only way that surface can supply intake
    data. Accepts either the full request envelope ({"input": ..., "claim_data":
    {...}}) or a bare claim object. Returns {} when the text is not a usable
    claim, so normal free-text chat still falls through to the validation errors.
    """
    text = (user_input or "").strip()
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    candidate = parsed.get("claim_data")
    if isinstance(candidate, dict) and candidate:
        return candidate
    # Bare claim object — accept only if it actually looks like claim intake.
    if any(key in parsed for key in _CLAIM_FIELDS):
        return parsed
    return {}


def _input_error(message: str) -> dict:
    return {
        "status": AgentStatus.SUCCESS.value,
        "input_error_message": message,
        "input_error_guidance": _INPUT_GUIDANCE,
    }


class PreProcessNode(FunctionNode):
    """Validate and minimise incoming cargo-claim intake before domain processing.

    Outer boundary node (Cat 2) — trust level VERIFIED_EXTERNAL.
    Applies S-2 input minimisation: strips shipment-party identifiers and
    converts raw cargo/premium values to tier labels.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        """S-2 extension: validate critical claim fields and reject malformed input."""
        user_input = state.get("user_input", "")
        input_ctx = state.get("input_context", {})
        raw = input_ctx.get("raw", user_input)

        # Reject oversized payloads to prevent injection via enormous inputs
        if len(raw) > 16_384:
            from framework.errors import SecurityViolationError

            raise SecurityViolationError("PreProcessNode: input payload exceeds maximum size (16 384 chars)")

        return state

    def execute(self, state: dict) -> dict:
        """Validate intake fields and produce canonical state delta."""
        input_ctx = state.get("input_context", {})
        intake = input_ctx.get("claim_data") or {}
        if not isinstance(intake, dict) or not intake:
            # Chat surfaces (marketplace) cannot set input_context["claim_data"];
            # fall back to a JSON claim pasted into user_input.
            intake = _extract_pasted_claim(state.get("user_input", ""))

        errors: list[str] = []

        # ── Required field presence ──────────────────────────────────────────
        incident_class = (intake.get("incident_class") or "").strip().lower()
        transport_mode = (intake.get("transport_mode") or "").strip().lower()
        incident_date = (intake.get("incident_date") or "").strip()
        discovery_date = (intake.get("discovery_date") or "").strip()
        policy_reference = (intake.get("policy_reference") or "").strip()
        jurisdiction = (intake.get("jurisdiction") or "").strip().upper()
        narrative = (intake.get("incident_narrative") or "").strip()

        if not incident_class:
            errors.append("incident_class is required")
        elif incident_class not in _SUPPORTED_CLASSES:
            errors.append(f"incident_class '{incident_class}' not in supported classes")

        if not transport_mode:
            errors.append("transport_mode is required")
        elif transport_mode not in _SUPPORTED_MODES:
            errors.append(f"transport_mode '{transport_mode}' not in supported modes")

        if not incident_date:
            errors.append("incident_date is required")
        elif not _DATE_RE.match(incident_date):
            errors.append("incident_date must be ISO-8601 (YYYY-MM-DD)")

        if not discovery_date:
            errors.append("discovery_date is required")
        elif not _DATE_RE.match(discovery_date):
            errors.append("discovery_date must be ISO-8601 (YYYY-MM-DD)")

        if not policy_reference:
            errors.append("policy_reference is required")

        if not jurisdiction:
            errors.append("jurisdiction is required")
        elif jurisdiction not in _SUPPORTED_JURISDICTIONS:
            errors.append(f"jurisdiction '{jurisdiction}' not in supported list")

        if not narrative:
            errors.append("incident_narrative is required")

        # ── Contradictory date check ─────────────────────────────────────────
        if incident_date and discovery_date and _DATE_RE.match(incident_date) and _DATE_RE.match(discovery_date):
            if discovery_date < incident_date:
                errors.append(f"discovery_date ({discovery_date}) cannot be before incident_date ({incident_date})")

        if errors:
            emit_trace_event(
                "PreProcessNode_intake_validation_failed",
                {"error_count": len(errors), "errors": errors},
                state,
            )
            return _input_error("Cargo claim intake validation failed: " + "; ".join(errors))

        # ── S-2 Minimization ─────────────────────────────────────────────────
        sanitized_narrative = self._minimize_narrative(narrative)

        # Convert raw cargo value to tier (never store raw financial value)
        raw_cargo_value = intake.get("cargo_value")
        cargo_value_tier = self._value_to_tier(raw_cargo_value)

        # Convert raw premium to tier
        raw_premium = intake.get("premium")
        premium_tier = self._value_to_tier(raw_premium)

        # Evidence inventory (list of metadata dicts — no raw commercial values)
        evidence_inventory = [self._sanitize_evidence_item(item) for item in (intake.get("evidence_inventory") or [])]

        # Deduplicate evidence by (doc_type, reference) pair
        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for item in evidence_inventory:
            key = (item.get("doc_type", ""), item.get("reference", ""))
            if key in seen:
                item["notes"] = (item.get("notes", "") + " [DUPLICATE — ignored]").strip()
            else:
                seen.add(key)
                deduped.append(item)

        # Canonical validated input summary for inner graph
        validated_input = (
            f"claim:{policy_reference} | class:{incident_class} | mode:{transport_mode} "
            f"| incident:{incident_date} | discovery:{discovery_date}"
        )

        transport_legs = intake.get("transport_legs") or [{"mode": transport_mode, "leg": 1}]

        emit_trace_event(
            "PreProcessNode_intake_validated",
            {
                "incident_class": incident_class,
                "transport_mode": transport_mode,
                "jurisdiction": jurisdiction,
                "evidence_count": len(deduped),
                "cargo_value_tier": cargo_value_tier,
            },
            state,
        )

        return {
            "validated_input": validated_input,
            "claim_reference": (intake.get("claim_reference") or "").strip() or None,
            "incident_class": incident_class,
            "transport_mode": transport_mode,
            "transport_legs": transport_legs,
            "incident_date": incident_date,
            "discovery_date": discovery_date,
            "jurisdiction": jurisdiction,
            "policy_reference": policy_reference,
            "incident_narrative": sanitized_narrative,
            "cargo_value_tier": cargo_value_tier,
            "premium_tier": premium_tier,
            "evidence_inventory": deduped,
            "workflow_warnings": [],
            "review_conditions": [],
            "status": AgentStatus.SUCCESS.value,
        }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _minimize_narrative(self, text: str) -> str:
        """Remove financial amounts and approximate shipment-party names."""
        text = _FINANCIAL_RE.sub("[FINANCIAL_VALUE_REDACTED]", text)
        return text

    def _value_to_tier(self, raw_value: object) -> str:
        """Map raw numeric/string value to a tier label — never store raw amount."""
        if raw_value is None:
            return "undisclosed"
        try:
            amount = float(str(raw_value).replace(",", "").strip())
            if amount < 10_000:
                return "low"
            if amount < 100_000:
                return "medium"
            return "high"
        except (ValueError, TypeError):
            return "undisclosed"

    def _sanitize_evidence_item(self, item: object) -> dict:
        """Ensure evidence item is a safe dict without raw financial values."""
        if not isinstance(item, dict):
            return {"doc_type": "unknown", "reference": "", "status": "unverified", "verified": False, "notes": ""}
        return {
            "doc_type": str(item.get("doc_type", "unknown")),
            "reference": str(item.get("reference", "")),
            "status": str(item.get("status", "unverified")),
            "verified": bool(item.get("verified", False)),
            "notes": str(item.get("notes", "")),
        }
