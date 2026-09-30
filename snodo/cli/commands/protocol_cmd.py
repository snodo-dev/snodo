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


def protocol_schema_command(json_output: bool = True) -> int:
    """Print the versioned Protocol model schema, enriched with live choices."""
    from snodo.protocols.schema import build_protocol_schema_publication

    publication = build_protocol_schema_publication()
    print(json.dumps(publication, indent=2, sort_keys=True))
    return 0
