from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph


def test_invalid_marketplace_request_returns_readable_guidance():
    graph = Graph(config={})
    graph.compile()
    result = graph.invoke(
        "Hello",
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"conversation_history": []},
    )
    assert result["status"] == "success"
    assert result["output"].startswith("Cargo claim preparation request could not be processed.")
    assert "claim_data" in result["output"]


def test_success_marketplace_output_is_readable_but_api_stays_structured():
    graph = Graph(config={})
    formatted_output = {
        "artifact_status": "preliminary_not_submitted",
        "assessment_posture": "review_required",
        "deadline_status": "open",
        "deadline_days_remaining": 12,
        "checklist_gaps_count": 1,
        "review_conditions": ["Confirm surveyor report."],
        "workflow_warnings": [],
        "claim_brief": "PRELIMINARY ONLY — prepare the evidence package for adjuster review.",
    }
    api_result = graph.get_output({"formatted_output": formatted_output})
    marketplace_result = graph.get_output(
        {"formatted_output": formatted_output, "input_context": {"conversation_history": []}}
    )
    assert api_result.get("output", api_result.get("formatted_output")) == formatted_output
    assert marketplace_result["output"].startswith("# Cargo Claim Preparation Brief")
    assert "Confirm surveyor report" in marketplace_result["output"]
    assert isinstance(marketplace_result["output"], str)
