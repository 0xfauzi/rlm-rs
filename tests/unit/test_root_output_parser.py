import pytest

from rlm_rs.orchestrator.root_prompt import parse_root_output


def test_root_output_parser_accepts_single_block() -> None:
    payload = "```repl\nprint('ok')\n```"

    assert parse_root_output(payload) == "print('ok')"


def test_root_output_parser_rejects_prefix_text() -> None:
    payload = "note\n```repl\nprint('ok')\n```"

    with pytest.raises(ValueError, match="only the repl code block"):
        parse_root_output(payload)


def test_root_output_parser_rejects_suffix_text() -> None:
    payload = "```repl\nprint('ok')\n```\nextra"

    with pytest.raises(ValueError, match="only the repl code block"):
        parse_root_output(payload)


def test_root_output_parser_rejects_multiple_blocks() -> None:
    payload = "```repl\nprint('one')\n```\n```repl\nprint('two')\n```"

    with pytest.raises(ValueError, match="exactly one repl code block"):
        parse_root_output(payload)


def test_root_output_parser_rejects_wrong_label() -> None:
    payload = "```python\nprint('ok')\n```"

    with pytest.raises(ValueError, match="exactly one repl code block"):
        parse_root_output(payload)
