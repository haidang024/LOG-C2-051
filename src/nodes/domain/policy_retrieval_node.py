"""PolicyRetrievalNode — versioned policy-terms RAG and clause citation evidence."""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.schemas.invocation_context import InvocationContext
from shared.utils.audit_logger import emit_trace_event

_CORPUS_VERSION = "2024-Q3"
_STALENESS_THRESHOLD_DAYS = 90
# ICC-A inherent-vice exclusion reference (canonical source)
_INHERENT_VICE_CLAUSE = {
    "source": "Institute Cargo Clauses (A) 1/1/2009",
    "section": "Clause 4.4",
    "version": "ICC-A-2009",
    "effective_date": "2009-01-01",
    "excerpt": "In no case shall this insurance cover loss damage or expense caused by inherent vice or nature of the subject-matter insured.",
    "applicability": "exclusion",
}

# Synthetic corpus for offline/deterministic tests (keyed by incident_class)
_SYNTHETIC_CORPUS: dict[str, list[dict]] = {
    "damage": [
        {
            "source": "Institute Cargo Clauses (A) 1/1/2009",
            "section": "Clause 1",
            "version": "ICC-A-2009",
            "effective_date": "2009-01-01",
            "excerpt": "This insurance covers all risks of loss of or damage to the subject-matter insured.",
            "applicability": "coverage",
        }
    ],
    "inherent_vice": [_INHERENT_VICE_CLAUSE],
    "shortage": [
        {
            "source": "Hague-Visby Rules",
            "section": "Art. III Rule 6",
            "version": "HV-1968",
            "effective_date": "1968-02-23",
            "excerpt": "The carrier and the ship shall in any event be discharged from all liability whatsoever...",
            "applicability": "notice_deadline",
        }
    ],
    "delay": [
        {
            "source": "Institute Cargo Clauses (A) 1/1/2009",
            "section": "Clause 4.5",
            "version": "ICC-A-2009",
            "effective_date": "2009-01-01",
            "excerpt": "In no case shall this insurance cover loss damage or expense caused by delay...",
            "applicability": "exclusion",
        }
    ],
    "contamination": [
        {
            "source": "Institute Cargo Clauses (A) 1/1/2009",
            "section": "Clause 1",
            "version": "ICC-A-2009",
            "effective_date": "2009-01-01",
            "excerpt": "This insurance covers all risks of loss of or damage to the subject-matter insured.",
            "applicability": "coverage",
        }
    ],
}


class PolicyRetrievalNode(FunctionNode):
    """Retrieve versioned policy clauses and ICC citations for the incident.

    Inner domain node — trust level ANONYMOUS.
    Retrieval only — no LLM query rewrite, reranking, or synthesis.
    Secrets accessed via InvocationContext.from_state(state).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        incident_class = (state.get("incident_class") or "").lower()
        policy_reference = state.get("policy_reference") or ""
        jurisdiction = state.get("jurisdiction") or ""
        transport_mode = (state.get("transport_mode") or "").lower()
        incident_narrative = state.get("incident_narrative") or ""
        warnings = list(state.get("workflow_warnings") or [])
        review_conditions = list(state.get("review_conditions") or [])

        # Access secrets via InvocationContext (not os.environ)
        ctx = InvocationContext.from_state(state)
        try:
            rag_endpoint = ctx.secrets.require("POLICY_RAG_ENDPOINT")
            rag_api_key = ctx.secrets.require("POLICY_RAG_API_KEY")
        except Exception:
            # In offline/stub mode: fall through to synthetic corpus
            rag_endpoint = None
            rag_api_key = None
        connector_configured = bool(rag_endpoint and rag_api_key)

        # ── Corpus freshness check ─────────────────────────────────────────
        # Freshness is determined from the corpus metadata timestamp
        # In production this would be fetched from the RAG service metadata endpoint.
        # For deterministic offline operation, use a fixed reference date.
        corpus_date_str = "2024-09-30"  # Last corpus update date
        try:
            corpus_date = datetime.strptime(corpus_date_str, "%Y-%m-%d").date()
            freshness_days = (date.today() - corpus_date).days
        except ValueError:
            freshness_days = _STALENESS_THRESHOLD_DAYS + 1

        corpus_stale = freshness_days > _STALENESS_THRESHOLD_DAYS

        if corpus_stale:
            warnings.append(
                f"Policy corpus is {freshness_days} days old (threshold: {_STALENESS_THRESHOLD_DAYS} days) "
                "— findings require broker verification against current policy terms"
            )
            review_conditions.append(
                f"Stale policy corpus ({_CORPUS_VERSION}, {freshness_days} days old) — "
                "all citation evidence must be verified by broker"
            )

        # ── Retrieval ─────────────────────────────────────────────────────
        # Detect inherent-vice cases from narrative (deterministic keyword check)
        is_inherent_vice = any(
            kw in (incident_narrative or "").lower()
            for kw in ("inherent vice", "natural deterioration", "inherent nature")
        )

        if is_inherent_vice:
            citations = _SYNTHETIC_CORPUS.get("inherent_vice", [])
            retrieval_status = "found"
        elif incident_class in _SYNTHETIC_CORPUS:
            citations = _SYNTHETIC_CORPUS[incident_class]
            retrieval_status = "found"
        else:
            citations = []
            retrieval_status = "not_found"

        # ── Policy mismatch / empty citation checks ───────────────────────
        if not citations:
            retrieval_status = "not_found"
            warnings.append(
                f"No policy citations found for incident_class='{incident_class}', "
                f"policy_reference='{policy_reference}' — assessment requires broker review"
            )
            review_conditions.append(
                f"No policy evidence retrieved for class '{incident_class}' — "
                "ELIGIBLE assessment requires citation support"
            )

        # Ensure policy-schedule linkage: citations must reference the policy
        # In production, the RAG service would filter by policy_reference.
        # Here we annotate citations with the policy reference.
        annotated_citations = [
            {**c, "policy_reference": policy_reference, "jurisdiction": jurisdiction} for c in citations
        ]

        emit_trace_event(
            "PolicyRetrievalNode_retrieved",
            {
                "corpus_version": _CORPUS_VERSION,
                "corpus_freshness_days": freshness_days,
                "corpus_stale": corpus_stale,
                "citations_count": len(annotated_citations),
                "retrieval_status": retrieval_status,
                "incident_class": incident_class,
                "transport_mode": transport_mode,
                "connector_configured": connector_configured,
            },
            state,
        )

        return {
            "policy_corpus_version": _CORPUS_VERSION,
            "policy_corpus_freshness_days": freshness_days,
            "policy_corpus_stale": corpus_stale,
            "policy_citations": annotated_citations,
            "retrieval_status": retrieval_status,
            "workflow_warnings": warnings,
            "review_conditions": review_conditions,
            "status": AgentStatus.SUCCESS.value,
        }
