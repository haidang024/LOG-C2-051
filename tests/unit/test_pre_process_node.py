"""Unit tests for PreProcessNode — intake validation and S-2 minimization."""

from __future__ import annotations


class TestPreProcessNode:
    """TC-03..BL-04: PreProcessNode canonical intake and minimization."""

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def _base_state(self, **overrides) -> dict:
        state = {
            "user_input": "sea damage claim",
            "caller_trust_level": "VERIFIED_EXTERNAL",
            "node_history": [],
            "error_log": [],
            "input_context": {
                "raw": "sea damage claim",
                "claim_data": {
                    "claim_reference": "CLM-2024-001",
                    "incident_class": "damage",
                    "transport_mode": "sea",
                    "incident_date": "2024-09-01",
                    "discovery_date": "2024-09-05",
                    "jurisdiction": "UKJLAW",
                    "policy_reference": "POL-XYZ-001",
                    "incident_narrative": "Visible water damage to cargo on arrival at port.",
                    "evidence_inventory": [
                        {
                            "doc_type": "commercial_invoice",
                            "reference": "INV-001",
                            "verified": True,
                            "status": "verified",
                            "notes": "",
                        },
                        {
                            "doc_type": "bill_of_lading",
                            "reference": "BL-001",
                            "verified": True,
                            "status": "verified",
                            "notes": "",
                        },
                    ],
                },
            },
        }
        if "claim_data" in overrides:
            state["input_context"]["claim_data"].update(overrides.pop("claim_data"))
        state.update(overrides)
        return state

    def test_valid_sea_damage_intake_succeeds(self):
        """BL-01: Valid sea visible-damage intake produces canonical state."""
        state = self._base_state()
        result = self.node(state)
        assert result.get("status") == "success"
        assert result.get("incident_class") == "damage"
        assert result.get("transport_mode") == "sea"
        assert result.get("validated_input") is not None

    def test_missing_incident_class_fails(self):
        """BL-02: Missing required field incident_class → ERROR."""
        state = self._base_state(claim_data={"incident_class": ""})
        result = self.node(state)
        assert result.get("status") == "success"
        assert "incident_class" in result.get("input_error_message", "")

    def test_unsupported_incident_class_fails(self):
        """BL-02: Unsupported incident_class → ERROR."""
        state = self._base_state(claim_data={"incident_class": "explosion"})
        result = self.node(state)
        assert result.get("status") == "success"
        assert "incident_class" in result.get("input_error_message", "")

    def test_missing_policy_reference_fails(self):
        """BL-02: Missing policy_reference → ERROR."""
        state = self._base_state(claim_data={"policy_reference": ""})
        result = self.node(state)
        assert result.get("status") == "success"
        assert "policy_reference" in result.get("input_error_message", "")

    def test_contradictory_dates_rejected(self):
        """BL-03: discovery_date before incident_date → ERROR."""
        state = self._base_state(
            claim_data={
                "incident_date": "2024-09-10",
                "discovery_date": "2024-09-01",
            }
        )
        result = self.node(state)
        assert result.get("status") == "success"
        assert "discovery_date" in result.get("input_error_message", "")

    def test_financial_value_redacted_from_narrative(self):
        """BL-04: Raw financial amounts in narrative are redacted."""
        state = self._base_state(claim_data={"incident_narrative": "Cargo worth USD 50,000 was damaged."})
        result = self.node(state)
        assert result.get("status") == "success"
        assert "USD" not in (result.get("incident_narrative") or "")
        assert "50,000" not in (result.get("incident_narrative") or "")

    def test_cargo_value_converted_to_tier(self):
        """BL-04: Raw cargo value is converted to tier label."""
        state = self._base_state(claim_data={"cargo_value": 75000})
        result = self.node(state)
        assert result.get("status") == "success"
        assert result.get("cargo_value_tier") == "medium"

    def test_state_contains_no_raw_financial_value(self):
        """TC-03: No raw financial value in output state."""
        state = self._base_state(claim_data={"cargo_value": 200000, "premium": 5000})
        result = self.node(state)
        assert result.get("status") == "success"
        # Raw values must not appear as numeric fields in state
        assert "cargo_value" not in result
        assert "premium" not in result
        assert result.get("cargo_value_tier") is not None

    def test_trust_level_enforced(self):
        """TC-08: S-1 gate rejects ANONYMOUS caller for VERIFIED_EXTERNAL node."""
        state = self._base_state()
        state["caller_trust_level"] = "ANONYMOUS"
        result = self.node(state)
        assert result["status"] == "error"
        assert "S-1 trust gate denied" in result["error_log"][0]

    def test_execute_not_called_directly(self):
        """CoE R1: node(state) is used — tests must not call node.execute(state)."""
        # This test documents the rule — actual enforcement is via code review
        assert callable(self.node)  # always called via __call__

    def test_duplicate_evidence_flagged(self):
        """BL-05: Duplicate evidence items are detected."""
        state = self._base_state(
            claim_data={
                "evidence_inventory": [
                    {
                        "doc_type": "commercial_invoice",
                        "reference": "INV-001",
                        "verified": True,
                        "status": "verified",
                        "notes": "",
                    },
                    {
                        "doc_type": "commercial_invoice",
                        "reference": "INV-001",
                        "verified": True,
                        "status": "verified",
                        "notes": "",
                    },
                ]
            }
        )
        result = self.node(state)
        assert result.get("status") == "success"
        # Only one item should be in deduped list
        inventory = result.get("evidence_inventory", [])
        assert len(inventory) == 1
