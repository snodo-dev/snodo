"""Snodo protocol package."""

import sys
from pathlib import Path
from typing import Dict, List, Optional

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


def _discover_templates() -> Dict[str, str]:
    """Derive the template registry from the files in the templates directory.

    Every ``*.yml`` file in ``snodo/protocols/templates/`` is a template; its
    stem is the selectable name.  Templates are parsed and verified (WF1–WF5)
    here, at import time, so a malformed shipped template fails loudly in CI
    rather than at a user's first ``snodo init``.
    """
    templates: Dict[str, str] = {}
    for path in sorted(_TEMPLATES_DIR.glob("*.yml")):
        name = path.stem
        raw = path.read_text()
        try:
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                raise ValueError("template root is not a mapping")
            protocol = Protocol(**data)
        except Exception as e:  # noqa: BLE001 — fail loudly on broken shipped template
            raise RuntimeError(
                f"Broken protocol template {path.name}: {e}"
            ) from e
        result = verify_protocol(protocol)
        if not result.passed:
            raise RuntimeError(
                f"Broken protocol template {path.name}: "
                f"well-formedness violations: {result.errors}"
            )
        _TEMPLATE_PROTOCOLS[name] = protocol
        templates[name] = raw
    return templates


# name → verified Protocol, populated by _discover_templates at import time.
_TEMPLATE_PROTOCOLS: Dict[str, Protocol] = {}


# name → raw YAML. Derived from the templates directory, so adding a ``.yml``
# file is sufficient to make a template selectable — no second edit anywhere.
PROTOCOL_TEMPLATES: Dict[str, str] = _discover_templates()


# Default protocol template
DEFAULT_PROTOCOL = PROTOCOL_TEMPLATES["team"]

# Alias for clarity
TEAM_PROTOCOL = DEFAULT_PROTOCOL

SOLO_PROTOCOL = PROTOCOL_TEMPLATES["solo"]

TWO_PLUS_N_PROTOCOL = PROTOCOL_TEMPLATES["2+n"]

INTENT_PROTOCOL = PROTOCOL_TEMPLATES["intent"]

BUGFIX_SURGEON_PROTOCOL = PROTOCOL_TEMPLATES["bugfix-surgeon"]

FEATURE_WARDEN_PROTOCOL = PROTOCOL_TEMPLATES["feature-warden"]

GREENFIELD_PROTOCOL = PROTOCOL_TEMPLATES["greenfield"]


def list_templates() -> List[str]:
    """Return the sorted list of selectable template names."""
    return sorted(PROTOCOL_TEMPLATES.keys())


def template_display_name(name: str) -> str:
    """Return the human-readable ``name`` field for a template."""
    return _TEMPLATE_PROTOCOLS[name].name


def template_protocol(name: str) -> Protocol:
    """Return the parsed, verified Protocol for a template by name.

    Raises:
        KeyError: If *name* is not a known template.
    """
    return _TEMPLATE_PROTOCOLS[name]


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
