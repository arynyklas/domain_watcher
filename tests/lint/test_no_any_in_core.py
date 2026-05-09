"""AST walker: fail if `Any` appears in any annotation under `core/`.

`Any` in core is a code smell, period. Allow-list nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

CORE_ROOT = Path(__file__).resolve().parents[2] / "src" / "domain_watcher" / "core"


def _is_any(node: ast.AST) -> bool:
    """Recursively check if an annotation node references `Any`.

    Catches:
    - bare `Any` -> ast.Name(id="Any")
    - `typing.Any` -> ast.Attribute(attr="Any")
    - inside subscripts like `list[Any]`, `Mapping[str, Any]` -> recurse
    - inside tuples like `tuple[int, Any]`
    - inside `Annotated[Any, ...]`
    """
    if isinstance(node, ast.Name) and node.id == "Any":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "Any":
        return True
    return any(_is_any(child) for child in ast.iter_child_nodes(node))


def _iter_annotations(tree: ast.AST) -> Iterator[tuple[ast.AST, int]]:
    """Yield (annotation_node, lineno) for every annotation site."""
    for node in ast.walk(tree):
        ann: ast.AST | None = None
        lineno = 0
        if isinstance(node, (ast.AnnAssign, ast.arg)):
            ann = node.annotation
            lineno = node.lineno
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ann = node.returns
            lineno = node.lineno
        if ann is not None:
            yield ann, lineno


def test_no_any_in_core() -> None:
    offenders: list[str] = []
    for path in CORE_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as e:  # pragma: no cover - guard
            offenders.append(f"{path}: syntax error: {e}")
            continue
        for ann, lineno in _iter_annotations(tree):
            if _is_any(ann):
                offenders.append(f"{path}:{lineno}: annotation references `Any`")
    assert not offenders, "Any in core:\n" + "\n".join(offenders)
