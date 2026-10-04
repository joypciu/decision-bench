from shipgate.comment import format_comment
from shipgate.review import Review, Risk


def test_comment_lists_each_file_risk():
    text = format_comment(
        Review(
            run_id="run-1",
            verdict="block",
            summary="Auth check was removed.",
            risks=[Risk("high", "auth.py", "Authentication was weakened.")],
            status="succeeded",
        )
    )
    assert text.startswith("**block**")
    assert "`auth.py`" in text
    assert "Authentication was weakened." in text
    assert "run-1" in text


def test_block_is_a_failing_commit_status():
    from shipgate.comment import status_for

    state, description = status_for(
        Review(
            run_id="run-1",
            verdict="block",
            summary="",
            risks=[Risk("high", "auth.py", "Authentication was weakened.")],
            status="succeeded",
        )
    )
    assert state == "failure"
    assert description == "block: auth.py"
