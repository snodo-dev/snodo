"""Send a test notification to every configured operator target."""

import typer

COMMAND_NAME = "notify"
app = typer.Typer(help="Manage runner notifications")


@app.command("test")
def notify_test() -> None:
    """Send a test message to every configured notification target."""
    from snodo.jobs.notifications import test_targets

    results = test_targets()
    if not results:
        typer.echo("No notification targets configured.")
        raise typer.Exit(1)
    for name, success in results:
        typer.echo(f"{'✓' if success else '✗'} {name}: {'sent' if success else 'delivery failed'}")
    if any(not success for _, success in results):
        raise typer.Exit(1)
