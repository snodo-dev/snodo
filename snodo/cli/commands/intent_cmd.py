"""Record and inspect local operator intents."""

import typer

COMMAND_NAME = "intent"
app = typer.Typer(invoke_without_command=True, help="Record and inspect intents")


@app.callback()
def _callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


def _store():
    from snodo.infrastructure.paths import require_project_root
    from snodo.infrastructure.intent_store import IntentStore
    return IntentStore(require_project_root())


def _error(exc: Exception, json_output: bool) -> int:
    if json_output:
        from snodo.cli.json_output import emit_json, schema_name
        emit_json({"schema": schema_name("intent"), "ok": False, "error": str(exc)}, 1)
        raise typer.Exit(1)
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(1)


@app.command("record")
def record(text: str = typer.Argument(...), issue: str | None = typer.Option(None, "--issue"),
           supersedes: str | None = typer.Option(None, "--supersedes"),
           json_output: bool = typer.Option(False, "--json")):
    """Record an intent."""
    try:
        item = _store().record(text, issue=issue, supersedes=supersedes)
    except Exception as exc:
        return _error(exc, json_output)
    if json_output:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({"schema": schema_name("intent"), "ok": True, "intent": item})
    typer.echo(f"Recorded {item['id']}: {item['text']}")
    return 0


@app.command("show")
def show(intent_id: str = typer.Argument(...), json_output: bool = typer.Option(False, "--json")):
    """Show a recorded intent."""
    try:
        item = _store().get(intent_id)
    except Exception as exc:
        return _error(exc, json_output)
    if json_output:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({"schema": schema_name("intent"), "ok": True, "intent": item})
    typer.echo(f"{item['id']}: {item['text']}\nCreated: {item['created_at']}")
    if item.get("issue"):
        typer.echo(f"Issue: {item['issue']}")
    if item.get("supersedes"):
        typer.echo(f"Supersedes: {item['supersedes']}")
    return 0


@app.command("list")
def list_intents(json_output: bool = typer.Option(False, "--json")):
    """List recorded intents, newest first."""
    try:
        items = _store().list()
    except Exception as exc:
        return _error(exc, json_output)
    if json_output:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({"schema": schema_name("intent"), "ok": True, "intents": items})
    for item in items:
        typer.echo(f"{item['id']}  {item['created_at']}  {item['text']}")
    return 0
