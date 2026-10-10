"""Read-only protocol schema publication commands."""

import json
from dataclasses import asdict
from pathlib import Path

import typer

COMMAND_NAME = "protocol"
app = typer.Typer(invoke_without_command=True, help="Inspect and work with protocol definitions")


@app.callback()
def _protocol_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@app.command(name="schema")
def protocol_schema(
    json_output: bool = typer.Option(False, "--json", help="Emit the protocol schema as JSON"),
):
    """Print the generated JSON Schema for protocol.yml."""
    return protocol_schema_command(json_output=json_output)


@app.command(name="validate")
def protocol_validate(
    path: str = typer.Argument(".snodo/protocol.yml", help="Protocol YAML file to validate"),
    json_output: bool = typer.Option(False, "--json", help="Emit findings as JSON"),
):
    """Validate a protocol YAML file without modifying it."""
    return protocol_validate_command(path=path, json_output=json_output)


@app.command(name="diff")
def protocol_diff(
    path: str = typer.Argument(".snodo/protocol.yml", help="Project protocol YAML file"),
    template: str | None = typer.Option(None, "--template", help="Compare against this shipped template"),
    json_output: bool = typer.Option(False, "--json", help="Emit the comparison as JSON"),
):
    """Show read-only drift between a project protocol and its shipped template."""
    return protocol_diff_command(path=path, template=template, json_output=json_output)


def protocol_validate_command(path: str = ".snodo/protocol.yml", json_output: bool = False) -> int:
    from snodo.protocol_validation import validate_protocol

    findings = validate_protocol(path)
    if json_output:
        print(json.dumps(findings, indent=2))
    elif not findings:
        print(f"{path}: valid")
    else:
        for finding in findings:
            print(f"{finding['path']}: {finding['severity']}: {finding['message']}")
    if any(finding["severity"] == "blocker" for finding in findings):
        raise typer.Exit(code=1)
    return 0


def protocol_diff_command(
    path: str = ".snodo/protocol.yml", template: str | None = None, json_output: bool = False,
) -> int:
    from snodo.protocols import (
        list_templates,
        load_protocol,
        resolve_protocol_template,
        template_protocol,
    )
    from snodo.protocols.diff import diff_protocols

    project = load_protocol(Path(path))
    if project is None:
        raise typer.Exit(code=2)
    template_name = template or resolve_protocol_template(project)
    if template_name is not None and template_name not in list_templates():
        print(f"Unknown template '{template_name}'. Available: {', '.join(list_templates())}")
        raise typer.Exit(code=2)
    if template_name is None:
        result = {"protocol": path, "template": None, "bespoke": True, "drift": None}
        if json_output:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"{path}: bespoke protocol; no shipped template matches it.")
            print("To compare with a template, use --template NAME. Adopt changes by editing the protocol file or running 'snodo init --force'.")
        return 0

    differences = diff_protocols(project, template_protocol(template_name))
    drift = asdict(differences)
    result = {
        "protocol": path,
        "template": template_name,
        "bespoke": False,
        "has_drift": not differences.is_empty,
        "drift": drift,
    }
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Protocol drift: {path} vs template '{template_name}'")
        _print_protocol_diff(drift)
        if differences.is_empty:
            print("No drift.")
        else:
            print("Adopt a change by editing the protocol file, or regenerate from the template with 'snodo init --force'.")
    if not differences.is_empty:
        raise typer.Exit(code=1)
    return 0


def _print_protocol_diff(drift: dict) -> None:
    groups = (
        ("Validators only in template", drift["validators_only_in_template"]),
        ("Validators added in project", drift["validators_only_in_project"]),
        ("Changed validators", drift["changed_validators"]),
        ("Modes added in project", drift["modes_added"]),
        ("Modes removed from template", drift["modes_removed"]),
        ("Changed modes", drift["changed_modes"]),
        ("Protocol settings", drift["settings"]),
        ("Project-local settings", drift["project_local"]),
    )
    for label, entries in groups:
        if entries:
            print(f"{label}:")
            print(json.dumps(entries, indent=2, sort_keys=True))


def protocol_schema_command(json_output: bool = True) -> int:
    """Print the versioned Protocol model schema, enriched with live choices."""
    from snodo.mcp.schema import build_protocol_schema_publication

    publication = build_protocol_schema_publication()
    print(json.dumps(publication, indent=2, sort_keys=True))
    return 0
