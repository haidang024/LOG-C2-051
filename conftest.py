"""Fallback framework stubs for local tests when the real wheel is unavailable.

When AgentCore is installed, module registration is bypassed so tests exercise
the real framework rather than replacing it through ``sys.modules``.
"""

from __future__ import annotations

import os
import sys
import types
import contextlib
from enum import Enum
from importlib.util import find_spec

_REAL_FRAMEWORK_AVAILABLE = find_spec("framework") is not None

ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _register_module(name: str) -> types.ModuleType:
    if _REAL_FRAMEWORK_AVAILABLE:
        return types.ModuleType(name)
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# ── langgraph stubs (register BEFORE any src imports that use langgraph) ──────
langgraph = _register_module("langgraph")
langgraph_graph = _register_module("langgraph.graph")
langgraph_graph.START = "START"
langgraph_graph.END = "END"
langgraph.graph = langgraph_graph

langgraph_types = _register_module("langgraph.types")
langgraph_types.interrupt = lambda value: value  # HITL stub — returns immediately
langgraph.types = langgraph_types

langgraph_errors = _register_module("langgraph.errors")


class GraphInterrupt(Exception):
    pass


langgraph_errors.GraphInterrupt = GraphInterrupt
langgraph.errors = langgraph_errors


# ── AgentStatus (str, Enum — NOT IntEnum) ─────────────────────────────────────
class AgentStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PENDING = "pending"
    HITL = "hitl"


# ── TrustLevel (int, Enum) ────────────────────────────────────────────────────
class TrustLevel(int, Enum):
    ANONYMOUS = 0
    VERIFIED_EXTERNAL = 1
    INTERNAL = 2


# ── Exceptions ────────────────────────────────────────────────────────────────
class SecurityViolationError(Exception):
    pass


class ConfigError(Exception):
    pass


class SubgraphError(Exception):
    pass


# ── AgentState (plain dict subclass) ─────────────────────────────────────────
class AgentState(dict):
    pass


# ── emit_trace_event (3-arg form) ─────────────────────────────────────────────
def emit_trace_event(event_type: str, payload: dict, state: dict | None = None) -> None:
    pass  # no-op stub; PB-6 monkeypatches base_node_mod.emit_trace_event


# ── framework.nodes.base_node (required by PB-6 monkeypatching) ──────────────
base_node_mod = _register_module("framework.nodes.base_node")
base_node_mod.emit_trace_event = emit_trace_event


class BaseNode:
    required_trust_level = None

    def __init__(self) -> None:
        pass

    def execute(self, state: dict) -> dict:
        raise NotImplementedError

    def __call__(self, state: dict) -> dict:
        """PB-6 invoke order: S-1 → node_start → S-2 → execute → S-3 → node_complete."""
        import framework.nodes.base_node as _bn

        # S-1 trust gate
        required = getattr(self.__class__, "required_trust_level", TrustLevel.ANONYMOUS)
        caller_trust_raw = state.get("caller_trust_level", TrustLevel.ANONYMOUS.value)
        if isinstance(caller_trust_raw, TrustLevel):
            caller_trust = caller_trust_raw
        else:
            try:
                caller_trust = TrustLevel(int(caller_trust_raw))
            except (ValueError, TypeError):
                caller_trust = TrustLevel.ANONYMOUS

        if required is not None and caller_trust < required:
            _bn.emit_trace_event("node_start", {}, state)
            _bn.emit_trace_event("node_complete", {}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"insufficient trust level: {caller_trust.name} < {required.name}"],
            }

        _bn.emit_trace_event("node_start", {}, state)
        try:
            state = self._security_gate_input(state)
        except SecurityViolationError as exc:
            _bn.emit_trace_event("node_complete", {}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"S-2 gate violation: {exc}"],
            }

        result = self.execute(state)

        try:
            result = self._security_gate_output(result)
        except SecurityViolationError as exc:
            _bn.emit_trace_event("node_complete", {}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"S-3 gate violation: {exc}"],
            }

        _bn.emit_trace_event("node_complete", {}, state)
        return result

    def _security_gate_input(self, state: dict) -> dict:
        if hasattr(self, "_extra_security_gate_input"):
            return self._extra_security_gate_input(state)
        return state

    def _security_gate_output(self, result: dict) -> dict:
        if hasattr(self, "_extra_security_gate_output"):
            return self._extra_security_gate_output(result)
        return result


base_node_mod.BaseNode = BaseNode


class FunctionNode(BaseNode):
    pass  # no __init__ — inherits BaseNode's no-arg __init__


class GraphNode(FunctionNode):
    """GraphNode stub that delegates execute() to get_subgraph().invoke()."""

    error_strategy = "propagate"
    propagate_hitl = False

    def __init__(self, config: dict | None = None, **kwargs) -> None:
        super().__init__()
        self._config = config or {}

    def get_subgraph(self):
        raise NotImplementedError

    def extract_input(self, state: dict) -> str:
        return state.get("validated_input", state.get("user_input", ""))

    def merge_output(self, state: dict, sub_result: dict) -> dict:
        return sub_result

    def execute(self, state: dict) -> dict:
        """Delegate to inner subgraph and merge output back into outer state."""
        subgraph = self.get_subgraph()
        user_input = self.extract_input(state)

        # Propagate config/context fields into subgraph via config injection
        cfg = self._config or {}
        inner_config_keys = [
            "checklist_version",
            "operator_config_id",
            "allowed_carrier_types",
            "allowed_jurisdictions",
            "expiry_warning_days",
            "screening_connector",
            "screening_list_version",
            "regulatory_corpus_version",
            "regulatory_corpus_endpoint",
            "report_language",
            "report_template_version",
        ]
        # Build inner state by copying relevant fields from outer state
        inner_state_patch = {k: cfg.get(k, state.get(k)) for k in inner_config_keys}

        # Create a merged state for the subgraph to start with
        combined_state = dict(state)
        for k, v in inner_state_patch.items():
            if v is not None:
                combined_state[k] = v

        # Use invoke() which runs nodes sequentially
        sub_result = subgraph.invoke(user_input, state=combined_state)
        return self.merge_output(state, sub_result)


# ── BaseGraph / AgentBaseGraph ─────────────────────────────────────────────────
class _SimpleStateGraph:
    def add_edge(self, _a, _b) -> None:
        pass

    def add_conditional_edges(self, _source, _fn, _mapping=None) -> None:
        pass


class BaseGraph:
    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._nodes: dict = {}
        self._sg = _SimpleStateGraph()
        # NOTE: _validate_config() NOT called here — tests need empty-config construction
        self.register_nodes()
        self.add_edges()

    def register_nodes(self) -> None:
        pass

    def add_edges(self) -> None:
        pass

    def route(self, state: dict) -> str:
        return "END"

    def get_output(self, state: dict) -> dict:
        return dict(state)

    def compile(self) -> "BaseGraph":
        return self

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx=None,
        input_context: dict | None = None,
        state: dict | None = None,  # allow pre-seeded state for inner graph calls
        **kwargs,
    ) -> dict:
        """Stub invoke — runs nodes sequentially via __call__ (not execute)."""
        if state is not None:
            current_state = dict(state)
            current_state["user_input"] = user_input
        else:
            current_state = {"user_input": user_input}

        if input_context:
            current_state["input_context"] = input_context
        if ctx is not None:
            current_state.update(
                {
                    "session_id": getattr(ctx, "session_id", session_id or ""),
                    "caller_trust_level": getattr(getattr(ctx, "caller_trust_level", None), "value", 1),
                    "caller_id": getattr(ctx, "caller_id", ""),
                }
            )
        for node in self._nodes.values():
            result = node(current_state)  # use __call__, not execute(), to trigger gates
            if isinstance(result, dict) and result.get("status") == AgentStatus.ERROR.value:
                current_state.update(result)
                break
            elif isinstance(result, dict):
                current_state.update(result)
        return self.get_output(current_state)


class _InitializeNode(FunctionNode):
    def execute(self, state: dict) -> dict:
        return {}


class _FinalizeNode(FunctionNode):
    def execute(self, state: dict) -> dict:
        return {}


class AgentBaseGraph(BaseGraph):
    def register_nodes(self) -> None:
        self._nodes["initialize"] = _InitializeNode()
        self._nodes["finalize"] = _FinalizeNode()

    def provision_secrets(self, provider) -> None:
        self._secrets_provider = provider


# ── Register all framework / shared modules ───────────────────────────────────
framework = _register_module("framework")

# framework.nodes
framework_nodes = _register_module("framework.nodes")
framework_nodes_base = base_node_mod
_register_module("framework.nodes.function_node").FunctionNode = FunctionNode
_register_module("framework.nodes.graph_node").GraphNode = GraphNode

# framework.graph
framework_graph = _register_module("framework.graph")
framework_graph_base = _register_module("framework.graph.base_graph")
framework_graph_base.BaseGraph = BaseGraph
framework_graph_agent = _register_module("framework.graph.agent_base_graph")
framework_graph_agent.AgentBaseGraph = AgentBaseGraph

# framework.schemas
framework_schemas = _register_module("framework.schemas")
_register_module("framework.schemas.agent_state").AgentState = AgentState
_register_module("framework.schemas.agent_status").AgentStatus = AgentStatus
_register_module("framework.schemas.trust_level").TrustLevel = TrustLevel


# framework.schemas.invocation_context
class InvocationContext:
    def __init__(
        self,
        session_id: str = "",
        caller_trust_level: TrustLevel = TrustLevel.VERIFIED_EXTERNAL,
        caller_id: str = "",
        hitl_allowed: bool = True,
    ) -> None:
        self.session_id = session_id
        self.caller_trust_level = caller_trust_level
        self.caller_id = caller_id
        self.hitl_allowed = hitl_allowed
        self.secrets = _SecretsHandle()
        self.correlation_id = ""

    @classmethod
    def from_state(cls, state: dict) -> "InvocationContext":
        trust_raw = state.get("caller_trust_level", TrustLevel.VERIFIED_EXTERNAL.value)
        if isinstance(trust_raw, TrustLevel):
            trust = trust_raw
        else:
            try:
                trust = TrustLevel(int(trust_raw))
            except (ValueError, TypeError):
                trust = TrustLevel.VERIFIED_EXTERNAL
        return cls(
            session_id=state.get("session_id", ""),
            caller_trust_level=trust,
            caller_id=state.get("caller_id", ""),
            hitl_allowed=state.get("hitl_allowed", True),
        )


class _SecretsHandle:
    def require(self, key: str) -> str:
        return f"STUB_SECRET_{key}"


_invoke_ctx_mod = _register_module("framework.schemas.invocation_context")
_invoke_ctx_mod.InvocationContext = InvocationContext

# framework.errors
framework_errors = _register_module("framework.errors")
framework_errors.SecurityViolationError = SecurityViolationError
framework_errors.ConfigError = ConfigError
framework_errors.SubgraphError = SubgraphError

# framework.secrets
framework_secrets = _register_module("framework.secrets")
_register_module("framework.secrets.context").bound_secrets = lambda p: contextlib.nullcontext()

# shared
shared = _register_module("shared")
shared_utils = _register_module("shared.utils")
shared_utils_audit = _register_module("shared.utils.audit_logger")
shared_utils_audit.emit_trace_event = emit_trace_event
shared_secrets = _register_module("shared.secrets")


def _mock_secrets_factory(**kwargs):
    class _Provider:
        pass

    return _Provider()


shared_secrets.factory = _mock_secrets_factory
