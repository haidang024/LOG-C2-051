"""PB-6: BaseNode invoke ordering and pre-execution S-1 denial."""

import importlib
import inspect
import pkgutil
from typing import ClassVar

import pytest
from framework.nodes.base_node import BaseNode
from framework.schemas.trust_level import TrustLevel


class _PrivilegedTrustGateFixture(BaseNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _security_gate_input(self, state):
        return state

    def execute(self, state):
        return {"status": "success"}

    def _security_gate_output(self, result):
        return result


def _trust_predecessor(required: TrustLevel) -> TrustLevel:
    predecessors = {
        TrustLevel.VERIFIED_EXTERNAL: TrustLevel.ANONYMOUS,
        TrustLevel.INTERNAL: TrustLevel.VERIFIED_EXTERNAL,
    }
    try:
        return predecessors[required]
    except KeyError as exc:
        raise AssertionError(f"no lower trust level defined for {required!r}") from exc


def _discover_node_classes() -> list[type]:
    """Import every module under src/nodes and collect concrete BaseNode subclasses."""
    try:
        package = importlib.import_module("src.nodes")
    except ImportError as exc:
        pytest.fail(f"PB-6 cannot import src.nodes; framework/template setup is broken: {exc}")

    discovered = []
    for _, module_name, _ in pkgutil.walk_packages(package.__path__, prefix="src.nodes."):
        module = importlib.import_module(module_name)
        for candidate in vars(module).values():
            if (
                isinstance(candidate, type)
                and issubclass(candidate, BaseNode)
                and candidate is not BaseNode
                and candidate.__module__ == module_name
                and not inspect.isabstract(candidate)
            ):
                discovered.append(candidate)
    return discovered


def _complete_state(trust: TrustLevel) -> dict:
    citation = {
        "source": "ICC-A-2009",
        "section": "Clause 1",
        "version": "ICC-A-2009",
        "effective_date": "2009-01-01",
        "excerpt": "Synthetic coverage fixture.",
        "applicability": "coverage",
        "policy_reference": "POL-PB6",
    }
    claim_data = {
        "claim_reference": "CLM-PB6",
        "incident_class": "damage",
        "transport_mode": "sea",
        "incident_date": "2026-08-01",
        "discovery_date": "2026-08-02",
        "jurisdiction": "SGLAW",
        "policy_reference": "POL-PB6",
        "incident_narrative": "Visible packaging damage after discharge.",
        "evidence_inventory": [],
    }
    return {
        "caller_trust_level": trust.value,
        "session_id": "pb6-session",
        "thread_id": "pb6-thread",
        "trace_id": "pb6-trace",
        "correlation_id": "pb6-invoke-order-test",
        "user_input": "prepare claim brief",
        "input_context": {"raw": "prepare claim brief", "claim_data": claim_data},
        "validated_input": "claim:POL-PB6 | class:damage | mode:sea",
        "node_history": [],
        "error_log": [],
        **claim_data,
        "transport_legs": [{"mode": "sea", "leg": 1}],
        "cargo_value_tier": "medium",
        "premium_tier": "undisclosed",
        "classification_result": {
            "class": "damage",
            "mode": "sea",
            "ambiguity_flags": [],
            "version": "1.0.0",
        },
        "checklist_required": [],
        "checklist_present": [],
        "checklist_gaps": [],
        "checklist_version": "1.3.0",
        "policy_corpus_version": "2024-Q3",
        "policy_corpus_stale": False,
        "policy_citations": [citation],
        "retrieval_status": "found",
        "assessment_posture": "ELIGIBLE",
        "assessment_reasons": ["Synthetic fixture"],
        "assessment_citations": [citation],
        "assessment_limitations": [],
        "assessment_version": "1.0.0",
        "deadline_status": "on_track",
        "deadline_date": "2026-08-05",
        "deadline_days_remaining": 3,
        "deadline_rule_version": "1.1.0",
        "deadline_rule_citation": "Synthetic rule",
        "deadline_timezone_assumption": "UTC",
        "claim_brief_draft": "PRELIMINARY ONLY — Synthetic broker-review brief.",
        "claim_brief_status": "preliminary_not_submitted",
        "output_bundle_version": "1.0.0",
        "workflow_warnings": [],
        "review_conditions": [],
    }


class TestInvokeOrder:
    def test_call_order_for_every_node(self, monkeypatch):
        node_classes = _discover_node_classes()
        if not node_classes:
            pytest.skip("no concrete BaseNode subclasses found under src/nodes")

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_class in node_classes:
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _order=order: _order.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_class, method_name)

                def spy(self, argument, _order=order, _label=label, _original=original):
                    _order.append(_label)
                    return _original(self, argument)

                monkeypatch.setattr(node_class, method_name, spy)

            node_class()(_complete_state(node_class.required_trust_level))
            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(f"{node_class.__name__}: expected {expected}, got {order}")

        assert not failures, "\n".join(failures)

    def test_s1_denial_refuses_execution_before_execute(self, monkeypatch):
        import framework.nodes.base_node as base_node_module

        events: list[str] = []
        execute_calls: list[object] = []
        monkeypatch.setattr(
            base_node_module,
            "emit_trace_event",
            lambda event_type, _payload, _state: events.append(event_type),
        )
        original_execute = _PrivilegedTrustGateFixture.execute

        def spy_execute(self, state):
            execute_calls.append(state)
            return original_execute(self, state)

        monkeypatch.setattr(_PrivilegedTrustGateFixture, "execute", spy_execute)
        result = _PrivilegedTrustGateFixture()(
            {
                "caller_trust_level": _trust_predecessor(_PrivilegedTrustGateFixture.required_trust_level).value,
                "correlation_id": "tc08-s1-denial",
            }
        )

        assert result["status"] == "error"
        assert "S-1 trust gate denied" in result["error_log"][0]
        assert events == ["s1_denied"]
        assert not execute_calls
