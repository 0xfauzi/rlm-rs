import pytest

from rlm_rs.sandbox.ast_policy import AstPolicyError, validate_source


def _assert_violation(source: str, rule: str) -> None:
    with pytest.raises(AstPolicyError) as excinfo:
        validate_source(source)

    assert any(violation.rule == rule for violation in excinfo.value.violations)


def test_ast_policy_allows_basic_code() -> None:
    validate_source("""total = sum([1, 2, 3])\nprint(total)""")


def test_ast_policy_rejects_imports() -> None:
    _assert_violation("import os", "import")
    _assert_violation("from os import path", "import")


def test_ast_policy_rejects_globals_and_nonlocal() -> None:
    _assert_violation(
        "def set_value():\n    global value\n    value = 1\n",
        "global",
    )

    _assert_violation(
        (
            "def outer():\n"
            "    value = 1\n"
            "    def inner():\n"
            "        nonlocal value\n"
            "        return value\n"
            "    return inner()\n"
        ),
        "nonlocal",
    )


def test_ast_policy_rejects_dunder_attribute_access() -> None:
    _assert_violation("x.__class__", "dunder_attribute")


def test_ast_policy_rejects_banned_names_and_modules() -> None:
    _assert_violation("eval('1 + 1')", "banned_name")
    _assert_violation("os.listdir('.')", "banned_module")
