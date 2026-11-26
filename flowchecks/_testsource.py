"""What the checks read from a test file: its test functions, the methods of its `Test*` classes, and skip markers."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field

_SKIP_MARKS = frozenset({"skip", "skipif", "xfail"})
_SKIP_CALLS = frozenset({"skip", "importorskip"})


@dataclass(frozen=True)
class ModuleTests:
    # `test_x` or `TestX::test_y` -> a fingerprint of its whole definition (body and decorators)
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
    context: list[ast.stmt] = []
    skipped = False
    for stmt in tree.body:
        is_test = isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name.startswith(
            "test"
        )
        is_test_class = isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test")
        if not is_test and not is_test_class:
            context.append(stmt)
            skipped = skipped or _is_module_skip(stmt)
    # What every test of the file depends on: imports, constants, helpers, `pytestmark`.
    module_context = _digest(ast.Module(body=context, type_ignores=[]))
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name.startswith(
            "test"
        ):
            functions[stmt.name] = f"{module_context}|{_digest(stmt)}"
        elif isinstance(stmt, ast.ClassDef):
            _read_class(stmt, "", functions, module_context)
    return ModuleTests(functions=functions, skipped=skipped)


def _digest(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _read_class(
    node: ast.ClassDef, prefix: str, functions: dict[str, str], module_context: str
) -> None:
    """`test*` methods of a `Test*` class, named `TestX::test_y` as pytest's node ID.

    A method's fingerprint holds its own definition and the class's context (decorators, bases,
    and attributes such as `pytestmark`), so a skip on the class breaks every method in it.
    Helper methods and fixtures are not part of the context and stay unlocked.
    """
    if not node.name.startswith("Test"):
        return
    name = f"{prefix}{node.name}"
    methods = (ast.FunctionDef, ast.AsyncFunctionDef)
    context = _digest(
        ast.Module(
            body=[
                *[s for s in node.body if not isinstance(s, (*methods, ast.ClassDef))],
                ast.Expr(ast.Tuple(elts=[*node.decorator_list, *node.bases], ctx=ast.Load())),
            ],
            type_ignores=[],
        )
    )
    for stmt in node.body:
        if isinstance(stmt, methods) and stmt.name.startswith("test"):
            own = _digest(stmt)
            functions[f"{name}::{stmt.name}"] = f"{module_context}|{context}|{own}"
        elif isinstance(stmt, ast.ClassDef):
            _read_class(stmt, f"{name}::", functions, module_context)
