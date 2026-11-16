from pathlib import Path

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo" / "web" / "static" / "protocol-pipeline.js"


def test_pipeline_renderer_uses_mockup_copy_and_safe_dom() -> None:
    source = SOURCE.read_text()
    assert "innerHTML" not in source
    for value in (
        '"Pipeline"', '"What a task passes through, in order. Click a validator chip to jump to its editor."',
        '"pre_execute"', '"Spec checks"', '"before any code is written"', '"coder"',
        '"Implement"', '"model comes from config"', '"post_execute"', '"Gate"',
        '"checks the work"', '"merge"', '"Deliver"', '"retries"',
        '"recovery depth"', '"fix attempts"', '"flow"', '"stage coder"',
    ):
        assert value in source
    for class_name in ("sec", "sec-h", "eyebrow", "sub", "card", "stage", "pill run", "pill abs", "pill pass", "h4", "dim", "chips", "mod", "off"):
        assert class_name in source


def test_pipeline_chips_follow_working_mode_and_open_row() -> None:
    source = SOURCE.read_text()
    assert "mode.validators" in source
    assert "validator.phase !== kind" in source
    assert "api.isChanged(id)" in source
    assert "state.openValidator = id" in source
    assert 'behavior: "smooth", block: "center"' in source
    assert "mode.tools" in source
    assert "execution.max_retries" in source
