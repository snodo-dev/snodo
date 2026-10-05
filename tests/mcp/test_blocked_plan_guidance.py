"""Guard guidance for fixing blocked plans forward in place."""

from pathlib import Path
import subprocess

from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.transport import _build_instructions
from snodo.protocols import template_protocol


ROOT = Path(__file__).resolve().parents[2]


def test_connect_time_instructions_prioritize_finishing_blocked_plans(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    server = ProtocolMCPServer(template_protocol("solo"), str(tmp_path), mode_id="producer")

    text = _build_instructions(server)

    assert "A blocked plan is unfinished work" in text
    assert "fix it forward within that same plan before starting new work" in text


def test_plan_loop_guidance_fixes_forward_in_same_plan():
    planning = (ROOT / "docs/authoring-a-plan.md").read_text(encoding="utf-8")
    outcomes = (ROOT / "docs/outcomes.md").read_text(encoding="utf-8")
    automation = (ROOT / "docs/running-unattended.md").read_text(encoding="utf-8")

    for text in (planning, outcomes, automation):
        normalized = " ".join(text.lower().split())
        assert "blocked plan is unfinished work" in normalized
        assert "same plan" in normalized
        assert "one-off" in normalized
        assert "every wave lands" in normalized
        assert "healthy" in normalized and "main" in normalized


def test_plan_guidance_markers_remain_present():
    for path, marker in (
        ("docs/authoring-a-plan.md", 'topic="planning"'),
        ("docs/outcomes.md", 'topic="outcomes"'),
        ("docs/running-unattended.md", 'topic="automation"'),
    ):
        assert marker in (ROOT / path).read_text(encoding="utf-8")
