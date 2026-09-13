"""Shared protocol and repository fixtures for survey's governed-repository tests.

FILE: tests/survey/protocol_fixtures.py

The same two protocol shapes are exercised at unit level (tests/survey/
test_drift.py) and through the CLI (tests/cli/test_survey_cmd.py), and the
ungoverned baseline is captured from a fixed repository layout. Keeping the
declarations here means the two levels test the same facts rather than two
hand-copied approximations of them.
"""

import json
from typing import Any, Dict, List, Optional

import yaml

# The repository whose survey output is pinned byte-for-byte in tests/survey/
# baseline/: a workspace declaring two packages, one with a real test script,
# and a decision-record directory.
UNGOVERNED_FIXTURE: Dict[str, str] = {
    "package.json": json.dumps(
        {"name": "baseline", "workspaces": ["packages/api", "packages/web"]}, indent=2
    ),
    "README.md": "# Baseline\n\nA fixture repository used to pin survey's ungoverned output.\n",
    "packages/api/package.json": json.dumps(
        {"name": "api", "scripts": {"test": "vitest run"}}, indent=2
    ),
    "packages/api/src/main.ts": "export const api = 1\n",
    "packages/api/src/server.ts": "export const serve = () => {}\n",
    "packages/web/package.json": json.dumps({"name": "web"}, indent=2),
    "packages/web/src/main.ts": "export const web = 1\n",
    "docs/decisions/001-use-a-monorepo.md": "# 001 Use a monorepo\n",
    "docs/decisions/002-two-packages.md": "# 002 Two packages\n",
}


def write_repository(root, files: Dict[str, str]) -> None:
    """Materialise a fixture repository from a {relative path: contents} map."""
    from pathlib import Path

    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def protocol_body(
    modules: Optional[List[Dict[str, Any]]] = None,
    validator_tooling: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """A well-formed protocol body, optionally declaring modules.

    ``modules=None`` means "no modules section at all" — the shape most real
    protocols have, and the one this comparison is easiest to get wrong on.
    """
    body: Dict[str, Any] = {
        "protocol_id": "monorepo",
        "name": "Monorepo Protocol",
        "version": "1.0.0",
        "initial_mode": "producer",
        "modes": [
            {
                "mode_id": "producer",
                "name": "Producer",
                "tools": ["edit"],
                "validators": ["quality", "adr"],
            },
            {
                "mode_id": "adjudicator",
                "name": "Adjudicator",
                "tools": ["approve", "judge"],
                "validators": ["quality", "adr"],
            },
        ],
        "validators": [
            {
                "validator_id": "quality",
                "validator_type": "quality",
                "criteria": ["tests pass"],
                "evaluation_phase": "post_execute",
                "tooling": validator_tooling or {},
            },
            {
                "validator_id": "adr",
                "validator_type": "architecture",
                "criteria": [
                    "The Architecture Decision Records in docs/adr apply here"
                ],
            },
        ],
    }
    if modules is not None:
        body["modules"] = modules
    return body


def protocol_yaml(**kwargs) -> str:
    return yaml.safe_dump(protocol_body(**kwargs), sort_keys=False)


# A repository the two-module protocol above describes honestly.
GOVERNED_FIXTURE: Dict[str, str] = {
    "services/api/pyproject.toml": "[project]\nname='api'\n[tool.pytest.ini_options]\n",
    "services/api/app.py": "x = 1\n",
    "apps/web/package.json": '{"name": "web", "scripts": {"test": "vitest run"}}',
    "apps/web/main.ts": "export const x = 1\n",
    "docs/adr/001-shape.md": "# 001\n",
}

MODULES_AGREE = [
    {
        "module_id": "api",
        "paths": ["services/api"],
        "decisions_path": "docs/adr",
        "tooling": {"test_command": "pytest"},
        "validators": ["quality"],
    },
    {"module_id": "web", "paths": ["apps/web"], "validators": ["quality"]},
]
