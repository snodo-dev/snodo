"""Guard the canonical plan-first guidance across orchestrator surfaces."""

from pathlib import Path
import subprocess
import re

import pytest

from snodo.mcp.guide import guide_text
from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.transport import _build_instructions
from snodo.protocols import template_protocol


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RULE = (
    "Group work into plans. A plan is one clear intention. Waves group its tasks: "
    "tasks in a wave run in parallel, and waves run in series. Queues group and "
    "schedule plans. Dispatch a single task only for a true one-off with no "
    "related work. A plan with one task in one wave adds nothing."
)
FORBIDDEN_GUIDANCE = (
    "smallest structure",
    "smallest fitting structure",
    "smallest-structure",
    "dispatch one task directly",
    "do not wrap it in a one-task plan",
    "use a plan only",
)


@pytest.fixture(scope="module")
def surfaces(tmp_path_factory):
    project = tmp_path_factory.mktemp("structure-guidance")
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    server = ProtocolMCPServer(template_protocol("solo"), str(project), mode_id="producer")
    exposed = {tool["name"] for tool in server.get_tools()}
    result = {
        "MCP connect-time instructions": _build_instructions(server),
        "guide default answer": guide_text(str(ROOT), exposed),
        "guide waves topic": guide_text(str(ROOT), exposed, "waves"),
        "guide mistakes topic": guide_text(str(ROOT), exposed, "mistakes"),
        "guide planning topic": guide_text(str(ROOT), exposed, "planning"),
        "guide automation topic": guide_text(str(ROOT), exposed, "automation"),
        "guide queues topic": guide_text(str(ROOT), exposed, "queues"),
        "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
        "docs/specs/mcp-self-description.md": (ROOT / "docs/specs/mcp-self-description.md").read_text(encoding="utf-8"),
    }
    return result


@pytest.mark.parametrize("surface", [
    "MCP connect-time instructions",
    "guide default answer",
    "guide waves topic",
    "guide mistakes topic",
    "guide planning topic",
    "guide automation topic",
    "guide queues topic",
    "README.md",
    "docs/specs/mcp-self-description.md",
])
def test_surface_contains_canonical_plan_guidance(surfaces, surface):
    text = surfaces[surface]
    normalized = " ".join(text.split())
    if surface in {"README.md", "docs/specs/mcp-self-description.md", "MCP connect-time instructions", "guide waves topic", "guide automation topic", "guide queues topic"}:
        assert " ".join(CANONICAL_RULE.split()) in normalized
    else:
        # The default response and these topics intentionally point to the
        # canonical modelling rule rather than duplicating the whole paragraph.
        expected_reference = {
            "guide default answer": "Plans are the default",
            "guide mistakes topic": "Follow the canonical rule above",
            "guide planning topic": "Start with one clear intention as a plan",
        }[surface]
        assert expected_reference.lower() in normalized.lower()


@pytest.mark.parametrize("surface", [
    "MCP connect-time instructions",
    "guide default answer",
    "guide waves topic",
    "guide mistakes topic",
    "guide planning topic",
    "guide automation topic",
    "guide queues topic",
    "README.md",
    "docs/specs/mcp-self-description.md",
])
@pytest.mark.parametrize("phrase", FORBIDDEN_GUIDANCE)
def test_surface_omits_drifted_plan_guidance(surfaces, surface, phrase):
    assert phrase not in surfaces[surface].lower()
