# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `Graph` (outer), `DomainWorkflowGraph` (inner)
- **L1 Base**: `AgentBaseGraph` (outer) + `BaseGraph` (inner)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

### Node Configuration

| Node | Location | Responsibility | L1 Base | Trust Level | Audit Event |
|------|----------|---------------|---------|-------------|-------------|
| `pre_process` | Outer | Intake validation, S-2 minimization (PII/financial), evidence deduplication | `FunctionNode` | `VERIFIED_EXTERNAL` | `PreProcessNode_intake_validated` |
| `main` (CargoClaimGraphNode) | Outer | Wrap inner DomainWorkflowGraph | `GraphNode` | (inherits outer trust) | (subgraph events) |
| `post_process` | Outer | Bundle output, S-3 redaction (PII, financial, definitive language) | `FunctionNode` | `VERIFIED_EXTERNAL` | `PostProcessNode_bundle_produced` |
| `classify` | Inner | Deterministic incident classification, evidence reconciliation | `FunctionNode` | `ANONYMOUS` | `ClassificationNode_classified` |
| `checklist` | Inner | Config-driven evidence checklist generation and gap analysis | `FunctionNode` | `ANONYMOUS` | `ChecklistNode_gaps_identified` |
| `policy_retrieval` | Inner | Versioned policy-terms RAG, ICC clause citation | `FunctionNode` | `ANONYMOUS` | `PolicyRetrievalNode_retrieved` |
| `assessment` | Inner | Preliminary coverage posture (ELIGIBLE/REVIEW_REQUIRED/LIKELY_EXCLUDED) | `FunctionNode` | `ANONYMOUS` | `AssessmentNode_posture_determined` |
| `deadline` | Inner | Deterministic notice deadline calculation and urgency alerts | `FunctionNode` | `ANONYMOUS` | `DeadlineNode_calculated` |
| `brief` | Inner | Structured Markdown claim brief generation with output redaction | `FunctionNode` | `ANONYMOUS` | `BriefNode_brief_produced` |

### Data Flow

```
Outer:
  START → initialize → pre_process → main (CargoClaimGraphNode)
             → post_process → finalize → END
                   ↓ (route: ERROR → END)
Inner (DomainWorkflowGraph):
  START → classify → checklist → policy_retrieval → assessment → deadline → brief → END
             ↓ (route: ERROR → END immediately after classify)
```

**Safe terminal routes:**
- Invalid classification → ERROR exit after `classify`
- Stale corpus / absent citations → `REVIEW_REQUIRED` posture, `review_blocked` artifact status
- Critical evidence gaps → `review_blocked` artifact status (brief still produced)
- All paths produce a claim brief or an error — no silent failures

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `claim_reference` | `Optional[str]` | Client claim reference (opaque ID) | No |
| `incident_class` | `Optional[str]` | damage/shortage/delay/total_loss/contamination | Yes (intake) |
| `transport_mode` | `Optional[str]` | sea/air/road/multimodal_standard | Yes (intake) |
| `transport_legs` | `Optional[list]` | List of leg dicts | No |
| `incident_date` | `Optional[str]` | ISO-8601 date | Yes (intake) |
| `discovery_date` | `Optional[str]` | ISO-8601 date | Yes (intake) |
| `jurisdiction` | `Optional[str]` | Law jurisdiction code | Yes (intake) |
| `policy_reference` | `Optional[str]` | Policy schedule ID | Yes (intake) |
| `incident_narrative` | `Optional[str]` | Sanitized incident description | Yes (intake) |
| `cargo_value_tier` | `Optional[str]` | low/medium/high/undisclosed (never raw amount) | No |
| `premium_tier` | `Optional[str]` | standard/enhanced/undisclosed (never raw amount) | No |
| `validated_input` | `Optional[str]` | Canonical intake summary for inner graph | Yes (pre_process) |
| `evidence_inventory` | `Optional[list]` | Evidence item metadata dicts | No |
| `classification_result` | `Optional[dict]` | class, mode, ambiguity_flags, version | Yes (classify) |
| `checklist_version` | `Optional[str]` | Checklist rule version | Yes (checklist) |
| `checklist_required` | `Optional[list]` | Required doc_types | Yes (checklist) |
| `checklist_present` | `Optional[list]` | Verified doc_types | Yes (checklist) |
| `checklist_gaps` | `Optional[list]` | Gap items with owner/action/rule_reference | Yes (checklist) |
| `broker_confirmation_required` | `Optional[bool]` | True for ambiguous/complex cases | Yes (checklist) |
| `policy_corpus_version` | `Optional[str]` | Corpus version used | Yes (retrieval) |
| `policy_corpus_freshness_days` | `Optional[int]` | Days since corpus update | Yes (retrieval) |
| `policy_corpus_stale` | `Optional[bool]` | True if > staleness threshold | Yes (retrieval) |
| `policy_citations` | `Optional[list]` | Citation dicts with source/section/version/excerpt/applicability | Yes (retrieval) |
| `retrieval_status` | `Optional[str]` | found/not_found/stale/mismatch | Yes (retrieval) |
| `assessment_posture` | `Optional[str]` | ELIGIBLE/REVIEW_REQUIRED/LIKELY_EXCLUDED | Yes (assessment) |
| `assessment_reasons` | `Optional[list]` | Reason strings | Yes (assessment) |
| `assessment_citations` | `Optional[list]` | Supporting citations | Yes (assessment) |
| `assessment_limitations` | `Optional[list]` | Evidence/data limitations | Yes (assessment) |
| `assessment_version` | `Optional[str]` | Assessment rule/prompt version | Yes (assessment) |
| `deadline_status` | `Optional[str]` | on_track/warning/urgent/overdue/review_required | Yes (deadline) |
| `deadline_date` | `Optional[str]` | Calculated deadline ISO-8601 | Yes (deadline) |
| `deadline_days_remaining` | `Optional[int]` | Days remaining (negative = overdue) | Yes (deadline) |
| `deadline_rule_version` | `Optional[str]` | Rule version applied | Yes (deadline) |
| `deadline_rule_citation` | `Optional[str]` | Clause/article reference | Yes (deadline) |
| `deadline_timezone_assumption` | `Optional[str]` | Timezone (UTC) | Yes (deadline) |
| `claim_brief_draft` | `Optional[str]` | Structured Markdown claim brief | Yes (brief) |
| `claim_brief_status` | `Optional[str]` | preliminary_not_submitted/review_blocked | Yes (brief) |
| `output_bundle_version` | `Optional[str]` | Template version | Yes (brief) |
| `workflow_warnings` | `Optional[list]` | Non-fatal warnings | Yes |
| `review_conditions` | `Optional[list]` | Active broker/insurer review conditions | Yes |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext reconstructed with `InvocationContext.from_state(state)` from framework-managed invocation fields; the context object is never stored in State
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)
- No raw cargo value, premium, or party identifiers in State

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, caller_trust_level, credential handle)
- [x] SecurityViolationError (raised in S-2/S-3 gate hooks)
- [x] S-2: `_extra_security_gate_input()` — implemented in `PreProcessNode`; rejects oversized payloads (>16 384 chars)
- [x] S-3: `_extra_security_gate_output()` — implemented in `PostProcessNode`; rejects definitive coverage language, raw financial values, and stripped disclaimers
- [x] S-4: `emit_trace_event()` — at least one domain-specific event in each `execute()`

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - Outer `FunctionNode` subclasses (`PreProcessNode`, `PostProcessNode`) → framework `@final` gate always runs automatically; extended via `_extra_security_gate_input()` / `_extra_security_gate_output()`
> - Inner `FunctionNode` subclasses (`ClassificationNode`, etc.) → framework gate runs; no domain extension needed (data already minimized at boundary)
> - `GraphNode` (`CargoClaimGraphNode`) → deliberate no-op (upstream node's gate already applied)

### Composition Pattern

- **Pattern**: GraphNode (subgraph) — outer `CargoClaimGraphNode` wraps inner `DomainWorkflowGraph`
- **Composition target**: `DomainWorkflowGraph` (6-node linear topology with conditional ERROR exit)
- **Error propagation strategy**: `propagate` — inner errors surface as SubgraphError to outer graph

### Runtime configuration and optional LLM injection

`src/api/server.py` loads `config/config.yaml` and provisions chained environment/domain secrets. Azure OpenAI clients are constructed per invocation by `src/services/llm_runtime.py`. The outer graph uses the framework-owned configuration property and registers the composite node as:

```python
self._nodes["main"] = CargoClaimGraphNode(
    config=self.config,
    llm=self.config.get("llm"),
)
```

`CargoClaimGraphNode._parent_config()` forwards both values to `DomainWorkflowGraph`. Tests prove object identity from server construction through the composite boundary. Missing optional LLM credentials do not prevent deterministic startup.

### Transparency and reviewer controls (EU AI Act Article 13 posture)

| Information exposed | Mechanism |
|---|---|
| Intended purpose and limitation | README, proposal, and mandatory `PRELIMINARY ONLY` disclaimer |
| Evidence basis | Versioned policy citations, corpus version, checklist version, and assessment reasons |
| Known limitations | Stale/missing corpus warnings, evidence gaps, ambiguity flags, and review conditions |
| Human oversight | Broker/insurer review is mandatory before any submission or decision |
| Traceability | Correlation/session metadata and domain trace events; credentials never enter State |

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework.*` and `shared.*` only; own `src.*`

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph (outer) + BaseGraph (inner) | AgentBaseGraph (flat Cat 1) | **Cat 2** | Multi-step domain workflow with 6 distinct nodes; each step is independently testable and has a separate security/audit responsibility |
| Composition pattern | GraphNode (subgraph) | RemoteAgentNode (HTTP) | **GraphNode** | Inner workflow is co-deployed; no cross-service HTTP boundary needed |
| LLM use scope | LLM drives posture enum | LLM drafts prose only; enum is deterministic | **LLM prose only** | Prevents LLM hallucination of coverage status; posture is safety-critical |
| Financial value handling | Store raw amount with PII label | Convert to tier at boundary | **Tier conversion** | Eliminates commercial data from State and checkpoints |
| Corpus staleness | Block on stale | Warn and force REVIEW_REQUIRED | **Warn + REVIEW_REQUIRED** | Preserves reviewer visibility of stale evidence rather than blocking pipeline |
| Deadline missing rule | Produce no deadline | Use default window + flag | **Default + flag** | Prevents silently missing deadlines; conservative default with explicit broker warning |
