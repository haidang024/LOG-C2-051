"""Marketplace chat has no structured request body — a pasted JSON claim must work.

Regression coverage for the chat surface: `input_context` carries only
`conversation_history`, so `input_context["claim_data"]` is never populated and
the operator's pasted JSON arrives in `user_input`.
"""

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

_CLAIM = {
    "claim_reference": "SMOKE-001",
    "incident_class": "damage",
    "transport_mode": "sea",
    "incident_date": "2026-08-01",
    "discovery_date": "2026-08-02",
    "jurisdiction": "SGLAW",
    "policy_reference": "POL-SMOKE-001",
    "incident_narrative": "Cargo packaging was visibly damaged after discharge.",
    "evidence_inventory": [],
}


def _run_chat(user_input: str) -> dict:
    graph = Graph(config={})
    graph.compile()
    return graph.invoke(
        user_input,
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"conversation_history": []},
    )


def test_pasted_request_envelope_is_accepted():
    envelope = {"input": "Prepare a preliminary cargo claim brief.", "claim_data": _CLAIM}
    result = _run_chat(json.dumps(envelope))
    assert result["status"] == "success"
    assert result["output"].startswith("# Cargo Claim Preparation Brief")
    assert "SMOKE-001" in result["output"]


def test_pasted_bare_claim_object_is_accepted():
    result = _run_chat(json.dumps(_CLAIM))
    assert result["status"] == "success"
    assert result["output"].startswith("# Cargo Claim Preparation Brief")


def test_plain_chat_text_still_returns_guidance():
    result = _run_chat("Hello, can you help me with a cargo claim?")
    assert result["status"] == "success"
    assert result["output"].startswith("Cargo claim preparation request could not be processed.")


def test_malformed_json_falls_through_to_guidance():
    result = _run_chat('{"claim_data": {"incident_class": "damage",')
    assert result["status"] == "success"
    assert result["output"].startswith("Cargo claim preparation request could not be processed.")


def test_unrelated_json_object_is_not_treated_as_claim():
    result = _run_chat('{"foo": "bar", "baz": 1}')
    assert result["status"] == "success"
    assert result["output"].startswith("Cargo claim preparation request could not be processed.")


def test_structured_claim_data_still_takes_precedence():
    """The /invoke path must be unaffected by the chat fallback."""
    graph = Graph(config={})
    graph.compile()
    result = graph.invoke(
        "Prepare a preliminary cargo claim brief for broker review.",
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"raw": "Prepare a brief.", "claim_data": _CLAIM},
    )
    assert result["status"] == "success"
    assert isinstance(result.get("output", result.get("formatted_output")), dict)
