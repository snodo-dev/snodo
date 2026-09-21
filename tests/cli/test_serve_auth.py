"""Authentication choices for managed serve tunnels."""

from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from snodo.cli.commands import serve_cmd


def test_auth_defaults_to_oauth_and_repeated_values_accumulate():
    assert serve_cmd._auth_methods(None) == ["oauth"]
    assert serve_cmd._auth_methods(["oauth", "service-token"]) == [
        "oauth", "service-token",
    ]


def test_invalid_auth_value_is_named():
    try:
        serve_cmd._auth_methods(["client-credentials"])
    except ValueError as error:
        assert "client-credentials" in str(error)
    else:  # pragma: no cover
        raise AssertionError("invalid auth value was accepted")


def test_provision_request_always_names_auth_set():
    response = MagicMock(status_code=200)
    response.json.return_value = {"hostname": "x.tunnel.snodo.dev", "tunnel_token": "t"}
    with patch.object(serve_cmd, "_get_cloud_tunnel_api_url", return_value="https://cloud"), \
         patch("httpx.post", return_value=response) as post:
        serve_cmd._provision_tunnel("key", "project", "all", "short", "1", auth=["oauth"])

    assert post.call_args.kwargs["json"]["auth"] == ["oauth"]


def test_service_token_credential_is_reported_once(capsys):
    serve_cmd._print_first_run_info(
        "x.tunnel.snodo.dev",
        ["oauth", "service-token"],
        {"client_id": "id.access", "client_secret": "secret"},
    )
    output = capsys.readouterr().out
    assert "id.access" in output
    assert output.count("CF-Access-Client-Secret: secret") == 1


def test_help_names_values_and_any_semantics():
    app = typer.Typer()
    serve_cmd.register(app)
    result = CliRunner().invoke(app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "oauth" in result.stdout
    assert "service-token" in result.stdout
    assert "repeat" in result.stdout
    assert "any accepted" in result.stdout
