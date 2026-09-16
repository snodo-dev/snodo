"""The planning surface and the job surface are one surface.

FILE: tests/mcp/test_job_surface_travels_with_work.py

A server that offers a tool which starts background work offers the
read-only tools that observe that work (issue #314): run_plan returns a
job_id and returns at once, and a job_id with no way to ask after it is
not a contract an orchestrator can honour. And the instructions a server
serves describe the tools that server actually has — a manual that names
an ungranted tool tells the orchestrator to call something it was not
given.

Observed in the wild: a seventeen-tool server, run_plan among them and
no get_job_status, instructed its orchestrator to poll a job it could
not see; the orchestrator worked around it by reading .snodo/jobs/<id>/
files off disk.

The protocol below grants NO mode the "dispatch" capability — the shape
that produced the bug. A second case pins the greenfield "plan" mode,
which grants "plan" without "dispatch" for a single server.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from snodo.compiler.models import Protocol
from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.tools import (
    JOB_OBSERVATION_TOOLS,
    PLANNING_TOOLS,
    TOOL_REGISTRY,
    WORK_STARTING_TOOLS,
)
from snodo.protocols import template_protocol
from snodo.mcp.transport import _assert_names_only_exposed_tools, _build_instructions

# plan + read only: run_plan is offered, dispatch never is.
NO_DISPATCH_PROTOCOL_DATA = {
    "protocol_id": "no_dispatch",
    "name": "No Dispatch Protocol",
    "version": "1.0.0",
    "initial_mode": "author",
    "modes": [
        {"mode_id": "author", "name": "Author", "tools": ["plan", "read"],
         "validators": ["quality"]},
        {"mode_id": "gate", "name": "Gate", "tools": ["decide"],
         "validators": ["quality"]},
    ],
    "validators": [
        {"validator_id": "quality", "validator_type": "quality",
         "tooling": {"test_command": "echo test passed"},
         "criteria": ["Tests pass"]},
    ],
    "disagreement_policy": "unanimous",
}


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, check=True)
    (Path(d) / "README.md").write_text("test")
    (Path(d) / ".snodo").mkdir()
    subprocess.run(["git", "add", "."], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _names(server):
    return {t["name"] for t in server.get_tools()}


def _named_tools(text):
    """Every TOOL_REGISTRY name appearing in the instruction prose."""
    return {
        name for name in TOOL_REGISTRY
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text)
    }


class TestJobToolsTravelWithRunPlan:
    def test_all_modes_server_without_any_dispatch_grant_still_offers_observers(
        self, project_dir
    ):
        """The bug's shape: no mode grants 'dispatch', yet the all-modes
        server always offers run_plan. The job observers must not be able to
        come apart from the starter."""
        server = ProtocolMCPServer(
            Protocol(**NO_DISPATCH_PROTOCOL_DATA), project_dir
        )
        names = _names(server)
        assert "run_plan" in names
        assert "dispatch_task" not in names  # no mutating tool was granted
        assert set(JOB_OBSERVATION_TOOLS) <= names

    def test_observers_are_callable_not_merely_listed(self, project_dir):
        """An exposed observer must actually answer — call_tool refuses
        only what the resolved set withholds."""
        server = ProtocolMCPServer(
            Protocol(**NO_DISPATCH_PROTOCOL_DATA), project_dir
        )
        assert server.call_tool("list_jobs", {}) == []
        with pytest.raises(Exception, match="Job not found"):
            server.call_tool("get_job_status", {"job_id": "j_missing"})
        with pytest.raises(Exception, match="Job not found"):
            server.call_tool("get_job_logs", {"job_id": "j_missing"})

    def test_pinned_plan_mode_gets_the_observers_with_its_run_plan(self, project_dir):
        """A single-mode server on the greenfield 'plan' mode: 'plan' grant,
        no 'dispatch' grant — same contract, same coupling."""
        server = ProtocolMCPServer(
            template_protocol("greenfield"), project_dir, mode_id="plan"
        )
        names = _names(server)
        assert "run_plan" in names
        assert set(JOB_OBSERVATION_TOOLS) <= names
        # Read-only travel only: nothing that mutates arrived uninvited.
        assert "dispatch_task" not in names
        assert "retry_job" not in names
        assert "write_file" not in names

    def test_mode_granting_no_planning_surface_is_unaffected(self, project_dir):
        """A mode with no 'plan' and no 'dispatch' keeps exactly its grant:
        no planning tools, no job tools, nothing dragged along."""
        server = ProtocolMCPServer(
            Protocol(**NO_DISPATCH_PROTOCOL_DATA), project_dir, mode_id="gate"
        )
        names = _names(server)
        assert "run_plan" not in names
        assert not (set(JOB_OBSERVATION_TOOLS) & names)
        assert not (set(PLANNING_TOOLS) & names)
        assert "propose_adjudicate" in names  # its own grant, intact
        assert "validate_task" in names       # the always-on meta-tool

    def test_dispatch_grant_still_carries_the_full_set(self, project_dir):
        """The 'dispatch' capability is untouched: starter plus observers
        plus the mutating retry it always granted."""
        server = ProtocolMCPServer(
            template_protocol("greenfield"), project_dir, mode_id="build"
        )
        names = _names(server)
        assert "dispatch_task" in names
        assert "retry_job" in names
        assert set(JOB_OBSERVATION_TOOLS) <= names


class TestInstructionsNameOnlyExposedTools:
    @pytest.mark.parametrize(
        "protocol_data, mode_id",
        [
            (NO_DISPATCH_PROTOCOL_DATA, None),
            (NO_DISPATCH_PROTOCOL_DATA, "author"),
            (NO_DISPATCH_PROTOCOL_DATA, "gate"),
        ],
        ids=["all-modes-no-dispatch", "plan-mode", "no-planning-mode"],
    )
    def test_instructions_list_only_tools_the_server_exposes(
        self, project_dir, protocol_data, mode_id
    ):
        server = ProtocolMCPServer(Protocol(**protocol_data), project_dir,
                                   mode_id=mode_id)
        instructions = _build_instructions(server)
        assert _named_tools(instructions) <= _names(server)

    def test_run_plan_server_offers_every_tool_its_instructions_name(
        self, project_dir
    ):
        """The ticket's central claim, stated as the consumer states it:
        follow the job_id — the server must be able to honour its own
        manual."""
        server = ProtocolMCPServer(
            Protocol(**NO_DISPATCH_PROTOCOL_DATA), project_dir
        )
        names = _names(server)
        assert "run_plan" in names
        instructions = _build_instructions(server)
        for tool in _named_tools(instructions):
            assert tool in names, f"instructions name {tool}; server does not expose it"

    def test_templates_produce_honest_instructions(self, project_dir):
        """Every shipped template, all-modes and per mode: the manual the
        server serves names nothing the server withholds."""
        for name in ("greenfield", "solo", "team", "2+n", "bugfix-surgeon",
                     "feature-warden", "intent"):
            proto = template_protocol(name)
            mode_ids = [None] + [m.mode_id for m in proto.modes]
            for mode_id in mode_ids:
                server = ProtocolMCPServer(proto, project_dir, mode_id=mode_id)
                instructions = _build_instructions(server)
                strays = _named_tools(instructions) - _names(server)
                assert not strays, (
                    f"{name} server mode={mode_id} instructs unexposed "
                    f"tools: {sorted(strays)}"
                )

    def test_guard_refuses_a_manual_naming_an_ungranted_tool(self):
        """The build-time tripwire itself: an edit that names an unexposed
        tool fails loudly at build, never as a lie served to a caller."""
        with pytest.raises(RuntimeError, match="does not expose"):
            _assert_names_only_exposed_tools(
                "Follow the job with get_job_status.", {"read_file"}
            )
        # A manual that names only what the server has passes.
        _assert_names_only_exposed_tools(
            "Follow the job with get_job_status.", {"read_file", "get_job_status"}
        )


class TestPairingIsDeclarative:
    def test_work_starting_map_pairs_only_read_only_tools(self):
        """The pairing table itself cannot become a back door: every tool
        that travels with a starter is a read-only observer, so exposing it
        costs no authority."""
        mutating = {"dispatch_task", "run_plan", "retry_job", "write_file",
                    "delete_file", "stage_files", "commit", "create_branch",
                    "merge_branch", "delete_branch", "create_pr",
                    "post_review_comment", "approve_pr", "reject_pr",
                    "merge_pr", "decompose", "generate_spec", "propose_plan"}
        for observers in WORK_STARTING_TOOLS.values():
            for tool in observers:
                assert tool not in mutating
                assert tool in TOOL_REGISTRY
