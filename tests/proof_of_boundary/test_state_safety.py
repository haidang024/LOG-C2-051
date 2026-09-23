# PB-2 + PB-5: State Safety Verification
# PB-5 applies only after checkpointing is enabled in config/config.yaml and the
# installed AgentCore exposes its pre-checkpoint ingress-protection hooks.

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Iterator
from typing import Any

import pytest

CREDENTIAL_FIELD_PATTERNS = re.compile(
    r"(jwt|token|api_key|secret|password|credential|connection_string)", re.IGNORECASE
)
PROHIBITED_TYPE_ANNOTATIONS = ["BaseModel", "InvocationContext"]
_RUNTIME_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _checkpointing_enabled() -> bool:
    if not _RUNTIME_CONFIG_PATH.exists():
        return False
    try:
        import yaml

        config = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text()) or {}
    except Exception:
        return False
    return bool(config.get("memory_enabled") or config.get("hitl", {}).get("enabled", False))


def _framework_ingress_protection_available() -> bool:
    try:
        from framework.graph.base_graph import BaseGraph
    except Exception:
        return False
    return all(hasattr(BaseGraph, hook) for hook in ("_sanitize_ingress", "_sanitize_resume_feedback"))


def _scan_state_file(filepath: str) -> list[str]:
    source = pathlib.Path(filepath).read_text()
    tree = ast.parse(source, filename=filepath)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id
                    if CREDENTIAL_FIELD_PATTERNS.search(field_name):
                        violations.append(f"{filepath}:{item.lineno} — Credential-like field name: {field_name}")
                    if item.annotation:
                        annotation_str = ast.dump(item.annotation)
                        for prohibited in PROHIBITED_TYPE_ANNOTATIONS:
                            if prohibited in annotation_str:
                                violations.append(f"{filepath}:{item.lineno} — Prohibited type in State: {prohibited}")
    return violations


def _walk_checkpoint_surface(value: Any, path: str = "checkpoint") -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_checkpoint_surface(key, f"{path}.<key>")
            yield from _walk_checkpoint_surface(child, f"{path}[{key!r}]")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(value):
            yield from _walk_checkpoint_surface(child, f"{path}[{index}]")


def _assert_raw_ingress_absent(surfaces: dict[str, Any], raw_values: dict[str, Any]) -> None:
    for surface, value in surfaces.items():
        for path, persisted in _walk_checkpoint_surface(value, surface):
            for name, raw in raw_values.items():
                if raw in (None, "", {}, []):
                    continue
                if persisted == raw:
                    pytest.fail(f"PB-5 raw {name} found at {path}; ingress crossed persistence boundary")
                if isinstance(raw, str) and isinstance(persisted, str) and raw in persisted:
                    pytest.fail(
                        f"PB-5 raw {name} found within string at {path}; " "ingress crossed persistence boundary"
                    )
                if isinstance(raw, dict):
                    for key, raw_value in raw.items():
                        if isinstance(raw_value, str) and isinstance(persisted, str) and raw_value in persisted:
                            pytest.fail(
                                f"PB-5 raw {name}[{key!r}] found within string at {path}; "
                                "ingress crossed persistence boundary"
                            )


class TestStateSafety:
    def test_state_file_safety(self):
        state_file = pathlib.Path(__file__).parents[2] / "src" / "schemas" / "state.py"
        if not state_file.exists():
            pytest.skip("src/schemas/state.py not found")
        violations = _scan_state_file(str(state_file))
        assert violations == [], "State safety violations found:\n" + "\n".join(violations)


_PB5_APPLICABLE = _checkpointing_enabled() and _framework_ingress_protection_available()
_PB5_WAIVER_REASON = (
    "config/config.yaml enables neither memory_enabled nor hitl.enabled — PB-5 auto-waived"
    if not _checkpointing_enabled()
    else "installed AgentCore lacks BaseGraph ingress hooks — PB-5 auto-waived pending framework cutover"
)


@pytest.mark.skipif(not _PB5_APPLICABLE, reason=_PB5_WAIVER_REASON)
def test_pb5_precheckpoint_ingress_not_raw() -> None:
    """PB-5 requires a real graph/checkpointer fixture once applicable."""
    pytest.fail(
        "PB-5 is applicable but this template has not wired its graph/checkpointer "
        "fixture and full persisted-surface assertions."
    )

    # checkpoint_tuple = checkpointer.get_tuple(config)
    # _assert_raw_ingress_absent(
    #     {"checkpoint": checkpoint_tuple.checkpoint,
    #      "metadata": checkpoint_tuple.metadata,
    #      "pending_writes": checkpoint_tuple.pending_writes},
    #     {"user_input": raw_user_input, "input_context": raw_input_context,
    #      "resume_feedback": raw_feedback},
    # )
