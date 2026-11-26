"""What the checks read from a test file: its module-level test functions and skip markers."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

_SKIP_MARKS = frozenset({"skip", "skipif", "xfail"})
_SKIP_CALLS = frozenset({"skip", "importorskip"})


@dataclass(frozen=True)
class ModuleTests:
    # function name -> a fingerprint of its whole definition (body and decorators)
    functions: dict[str, str] = field(default_factory=dict)
    # the module as a whole is skipped (`pytestmark`, `pytest.skip(allow_module_level=True)`)
    skipped: bool = False


def make_id(path: str, name: str) -> str:
    return f"{path}::{name}"


def _has_skip_mark(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr in _SKIP_MARKS for n in ast.walk(node))


def _is_module_skip(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Assign | ast.AnnAssign):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        named = any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets)
        return named and stmt.value is not None and _has_skip_mark(stmt.value)
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _SKIP_CALLS
        for n in ast.walk(stmt)
    )


def parse(source: str | None) -> ModuleTests:
    """Parse a test file. A missing or unparsable file holds no test."""
    if source is None:
        return ModuleTests()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ModuleTests()
    functions: dict[str, str] = {}
    skipped = False
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            if stmt.name.startswith("test"):
                functions[stmt.name] = ast.dump(stmt, include_attributes=False)
        elif not isinstance(stmt, ast.ClassDef) and _is_module_skip(stmt):
            skipped = True
    return ModuleTests(functions=functions, skipped=skipped)
