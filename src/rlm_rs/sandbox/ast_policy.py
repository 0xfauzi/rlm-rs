from __future__ import annotations

import ast
from dataclasses import dataclass

BANNED_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "help",
    }
)

BANNED_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "pathlib",
        "shutil",
        "urllib",
        "requests",
        "http",
    }
)

ALLOWED_BUILTINS: frozenset[str] = frozenset(
    {
        "len",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "reversed",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "int",
        "float",
        "str",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "isinstance",
        "print",
    }
)


@dataclass(frozen=True)
class AstViolation:
    rule: str
    message: str
    line: int | None = None
    col: int | None = None


class AstPolicyError(ValueError):
    def __init__(self, violations: list[AstViolation]) -> None:
        message = "; ".join(_format_violation(violation) for violation in violations)
        super().__init__(message)
        self.violations = violations


def _node_location(node: ast.AST) -> tuple[int | None, int | None]:
    return getattr(node, "lineno", None), getattr(node, "col_offset", None)


def _format_violation(violation: AstViolation) -> str:
    if violation.line is None:
        return violation.message
    col = 0 if violation.col is None else violation.col
    return f"{violation.message} at {violation.line}:{col}"


def collect_violations(tree: ast.AST) -> list[AstViolation]:
    violations: list[AstViolation] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            line, col = _node_location(node)
            violations.append(
                AstViolation("import", "Import statements are not allowed", line, col)
            )
            continue

        if isinstance(node, ast.Global):
            line, col = _node_location(node)
            violations.append(AstViolation("global", "Global is not allowed", line, col))
            continue

        if isinstance(node, ast.Nonlocal):
            line, col = _node_location(node)
            violations.append(
                AstViolation("nonlocal", "Nonlocal is not allowed", line, col)
            )
            continue

        if isinstance(node, ast.Attribute) and "__" in node.attr:
            line, col = _node_location(node)
            violations.append(
                AstViolation(
                    "dunder_attribute",
                    f"Dunder attribute access is not allowed: {node.attr}",
                    line,
                    col,
                )
            )
            continue

        if isinstance(node, ast.Name):
            if node.id in BANNED_NAMES:
                line, col = _node_location(node)
                violations.append(
                    AstViolation(
                        "banned_name",
                        f"Banned name is not allowed: {node.id}",
                        line,
                        col,
                    )
                )
                continue

            if node.id in BANNED_MODULE_NAMES:
                line, col = _node_location(node)
                violations.append(
                    AstViolation(
                        "banned_module",
                        f"Banned module name is not allowed: {node.id}",
                        line,
                        col,
                    )
                )
                continue

    return violations


def validate_ast(tree: ast.AST) -> None:
    violations = collect_violations(tree)
    if violations:
        raise AstPolicyError(violations)


def validate_source(source: str) -> None:
    tree = ast.parse(source, mode="exec")
    validate_ast(tree)
