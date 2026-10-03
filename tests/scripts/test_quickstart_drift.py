"""Keep first-run instructions canonical and guard the quickstart sequence."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_COMMANDS = (
    'git commit -m "Initial project commit"',
    "pip install snodo",
    "pip install pytest",
    'snodo init --template team --test-command "PYTHONPATH=. pytest" --yes',
    "snodo run \"add a hello() function that returns the string 'world', with a test\" --mock",
)


def _command_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:bash|sh)\s*\n(.*?)\n```", text, re.DOTALL)


def test_first_run_pages_link_to_the_canonical_quickstart():
    for page, link in (
        ("README.md", "docs/runbook.md#quickstart"),
        ("docs/index.md", "runbook.md#quickstart"),
    ):
        text = (REPO_ROOT / page).read_text(encoding="utf-8")
        assert link in text, f"{page} must link to {link}"
        for block in _command_blocks(text):
            assert not any(command in block for command in QUICKSTART_COMMANDS), (
                f"{page} duplicates the canonical quickstart command block"
            )


def test_runbook_quickstart_preserves_the_first_run_command_order():
    text = (REPO_ROOT / "docs/runbook.md").read_text(encoding="utf-8")
    match = re.search(
        r"^## Quickstart\s*$([\s\S]*?)(?=^## |\Z)", text, re.MULTILINE
    )
    assert match, "docs/runbook.md must have a Quickstart section"
    blocks = _command_blocks(match.group(1))
    assert blocks, "Quickstart must contain a shell command block"
    block = "\n".join(blocks)
    positions = [block.find(command) for command in QUICKSTART_COMMANDS]
    assert all(position >= 0 for position in positions), (
        "Quickstart is missing an essential first-run command"
    )
    assert positions == sorted(positions), (
        "Quickstart commands must remain ordered: commit, install Snodo, install "
        "pytest, initialize with the test command, then run the mock task"
    )
