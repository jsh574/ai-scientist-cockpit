from pathlib import Path


def test_workflow_a_exposes_and_consumes_feedback_input() -> None:
    workflow = (
        Path(__file__).parents[1]
        / "dify"
        / "Planning Design Candidate Generator.yml"
    ).read_text(encoding="utf-8")

    assert "label: _feedback" in workflow
    assert "variable: _feedback" in workflow
    assert "_feedback: str = \"\"" in workflow
    assert '"iteration_feedback": feedback' in workflow
    assert "- _feedback\n          variable: _feedback" in workflow
    assert "iteration_feedback 非空时必须优先响应" in workflow
    assert "required: false\n          type: paragraph\n          variable: _feedback" in workflow
