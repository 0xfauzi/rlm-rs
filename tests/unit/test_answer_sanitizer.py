from rlm_rs.orchestrator.worker import _sanitize_final_answer


def test_sanitize_removes_supporting_points_section() -> None:
    answer = (
        "Main line\n"
        "\n"
        "Supporting points mentioned in the document (selected snippets):\n"
        "- The ROI of\n"
        "- wave of AI-driven business value.\n"
        "\n"
        "Tail"
    )
    expected = "Main line\n\nTail"
    assert _sanitize_final_answer(answer) == expected


def test_sanitize_no_change_without_header() -> None:
    answer = "One\n- Two"
    assert _sanitize_final_answer(answer) == answer
