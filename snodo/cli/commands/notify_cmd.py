"""Send a test notification to every configured operator target."""

import sys

import typer

COMMAND_NAME = "notify"
app = typer.Typer(help="Manage runner notifications", hidden=True)


@app.command("test")
def notify_test() -> None:
    """Send a test message to every configured notification target."""
    print(
        "Note: 'snodo notify test' is deprecated. Use 'snodo config --notify-test' instead.",
        file=sys.stderr,
    )
    raise typer.Exit(run_notify_test())


def run_notify_test() -> int:
    """Send a test message to every configured target and report delivery results."""
    from snodo.jobs.notifications import test_targets

    results = test_targets()
    if not results:
        typer.echo("No notification targets configured.")
        return 1
    for name, success in results:
        typer.echo(f"{'✓' if success else '✗'} {name}: {'sent' if success else 'delivery failed'}")
    if any(not success for _, success in results):
        return 1
    return 0
