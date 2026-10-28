"""redesign U1: commitments.py alone computes "how much of a group is committed"."""

import ast
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
REMOVED = {"group_allocated", "group_own_allocated", "group_committed", "fresh_group_committed"}
DISPLAY_HELPERS: set[str] = set()  # a display-only helper that may keep an old name (none today)


def _names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute):
            names.add(node.id if isinstance(node, ast.Name) else node.attr)
        elif isinstance(node, ast.ImportFrom | ast.Import):
            names.update(a.name.split(".")[-1] for a in node.names)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


def test_no_service_uses_the_old_commitment_helpers():
    offenders = {}
    for path in sorted(SERVICES.glob("*.py")):
        found = (_names(ast.parse(path.read_text())) & REMOVED) - DISPLAY_HELPERS
        if found:
            offenders[path.name] = sorted(found)
    assert offenders == {}


def test_validation_has_no_group_tree_check():
    # the per-parent tree check lives in commitments (children at max(budget, spent), consumption)
    assert "check_group_tree" not in _names(ast.parse((SERVICES / "validation.py").read_text()))
    assert "check_group_tree" in _names(ast.parse((SERVICES / "commitments.py").read_text()))
