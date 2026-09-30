"""Generate a deterministic copy of the public protocol schema publication."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "protocol-v1.json"


def publication() -> dict:
    """Return the CLI publication, ignoring installed version and providers."""
    from snodo.cli.commands.protocol_cmd import protocol_schema_command

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        protocol_schema_command()
    value = json.loads(output.getvalue())
    # Provider installations vary per developer and deployment. They are runtime
    # suggestions, not part of the Protocol model contract. Keep other choices
    # (such as coders, validators and templates) in the snapshot.
    value["snodo_version"] = "<release-version>"
    value["schema"]["x-snodo-choices"]["providers"] = []
    return value


def render() -> str:
    return json.dumps(publication(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--write":
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(render(), encoding="utf-8")
        print(f"Updated {SCHEMA_PATH.relative_to(REPO_ROOT)}")
        return 0
    print(render(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
