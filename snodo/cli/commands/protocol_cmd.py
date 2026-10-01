"""Read-only protocol schema publication commands."""

import json

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


def protocol_schema_command(json_output: bool = True) -> int:
    """Print the versioned Protocol model schema, enriched with live choices."""
    from snodo.mcp.schema import build_protocol_schema_publication

    publication = build_protocol_schema_publication()
    print(json.dumps(publication, indent=2, sort_keys=True))
    return 0
