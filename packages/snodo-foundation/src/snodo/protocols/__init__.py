"""Snodo protocol package."""

import sys
from pathlib import Path
from typing import Optional

import yaml

from snodo.compiler.models import Protocol
from snodo.compiler.verifier import verify_protocol, ProtocolWellFormednessError


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> str:
    """Load a protocol template YAML file from disk.

    Templates live as standalone YAML files in snodo/protocols/templates/.
    This replaces the previous approach of embedding templates as Python
    triple-quoted string constants, making protocols reviewable documents
    and editable without code changes (Paper Section 6.4).

    Args:
        name: Template name without extension (e.g., "solo", "team", "2+n")

    Returns:
        Raw YAML content as a string
    """
    return (_TEMPLATES_DIR / f"{name}.yml").read_text()


# Default protocol template
DEFAULT_PROTOCOL = _load_template("team")

# Alias for clarity
TEAM_PROTOCOL = DEFAULT_PROTOCOL

SOLO_PROTOCOL = _load_template("solo")

TWO_PLUS_N_PROTOCOL = _load_template("2+n")

INTENT_PROTOCOL = _load_template("intent")

PROTOCOL_TEMPLATES = {
    "solo": SOLO_PROTOCOL,
    "team": TEAM_PROTOCOL,
    "2+n": TWO_PLUS_N_PROTOCOL,
    "intent": INTENT_PROTOCOL,
}


def load_protocol(protocol_path: Path) -> Optional[Protocol]:
    """Load, parse, and verify protocol from YAML file.

    Runs all WF1-WF5 well-formedness checks after parsing.
    """
    try:
        with open(protocol_path) as f:
            data = yaml.safe_load(f)

        protocol = Protocol(**data)

        # WF1-WF5 verification (Section 4.4)
        result = verify_protocol(protocol)
        if not result.passed:
            raise ProtocolWellFormednessError(result.errors)

        return protocol

    except FileNotFoundError:
        print(f"Error: Protocol file not found: {protocol_path}", file=sys.stderr)
        print("Run 'snodo init' to create default protocol.", file=sys.stderr)
        return None
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in protocol file: {e}", file=sys.stderr)
        return None
    except ProtocolWellFormednessError as e:
        print(f"Error: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: Failed to parse protocol: {e}", file=sys.stderr)
        return None
