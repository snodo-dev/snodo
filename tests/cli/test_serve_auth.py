"""Authentication choices for managed serve tunnels."""

import re
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
    # Rich colours help output when it detects CI (GITHUB_ACTIONS) and styles
    # "-" and "-verbose" separately, so match on the text without ANSI codes.
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)

    assert result.exit_code == 0
    assert "oauth" in out
    assert "service-token" in out
    assert "repeat" in out
    assert "any accepted" in out
    assert "--verbose" in out


def test_mcp_server_arms_and_disarms_liveness_when_transport_exits(monkeypatch):
    from types import SimpleNamespace

    server = MagicMock()
    server.get_tools.return_value = ["tool"]
    mcp = MagicMock()
    mcp.settings = SimpleNamespace()
    mcp.run.side_effect = RuntimeError("transport stopped")
    protocol = SimpleNamespace(protocol_id="test", get_mode=lambda _: True)
    calls = []

    monkeypatch.setattr("snodo.mcp.server.ProtocolMCPServer", lambda **_: server)
    monkeypatch.setattr("snodo.mcp.transport.build_fastmcp_server", lambda *_args, **_kwargs: mcp)
    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: "/project")
    monkeypatch.setattr(serve_cmd, "_load_tunnel_config", lambda _: {})
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.install", lambda: calls.append("install"))
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.uninstall", lambda: calls.append("uninstall"))

    try:
        serve_cmd._run_server(
            SimpleNamespace(protocol="protocol.yml", mode=None, transport="stdio", port=None),
            protocol,
        )
    except RuntimeError as error:
        assert str(error) == "transport stopped"
    else:  # pragma: no cover
        raise AssertionError("transport exception was swallowed")
    assert calls == ["install", "uninstall"]


def test_serve_startup_prints_local_cloud_sync_notice(monkeypatch, capsys):
    from types import SimpleNamespace

    server = MagicMock()
    server.get_tools.return_value = []
    mcp = MagicMock()
    mcp.settings = SimpleNamespace()
    protocol = SimpleNamespace(protocol_id="test", get_mode=lambda _: True)
    monkeypatch.setattr("snodo.mcp.server.ProtocolMCPServer", lambda **_: server)
    monkeypatch.setattr("snodo.mcp.transport.build_fastmcp_server", lambda *_a, **_k: mcp)
    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: "/project")
    monkeypatch.setattr(serve_cmd, "_load_tunnel_config", lambda _: {})
    monkeypatch.setattr("snodo.cli.commands.cloud_status.sync_issue_notice", lambda: "Cloud sync needs attention: old-session (1456 unsent, refused). Run `snodo cloud sync --all --force`.")
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.install", lambda: None)
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.uninstall", lambda: None)

    serve_cmd._run_server(
        SimpleNamespace(protocol="protocol.yml", mode=None, transport="stdio", port=None),
        protocol,
    )
    assert "old-session (1456 unsent, refused)" in capsys.readouterr().err


def test_tunnel_hostname_is_added_to_http_host_allowlist(monkeypatch):
    from types import SimpleNamespace

    server = MagicMock()
    server.get_tools.return_value = []
    mcp = MagicMock()
    mcp.settings = SimpleNamespace(transport_security=SimpleNamespace(), port=None)
    protocol = SimpleNamespace(protocol_id="test", get_mode=lambda _: True)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "")
    monkeypatch.setattr("snodo.mcp.server.ProtocolMCPServer", lambda **_: server)
    monkeypatch.setattr("snodo.mcp.transport.build_fastmcp_server", lambda *_a, **_k: mcp)
    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: "/project")
    monkeypatch.setattr(serve_cmd, "_load_tunnel_config", lambda _: {
        "hostname": "safe.tunnel.snodo.dev", "auth": ["service-token"],
    })
    monkeypatch.setattr(serve_cmd, "_choose_serve_port", lambda _: 8080)
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.install", lambda: None)
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.uninstall", lambda: None)

    serve_cmd._run_server(SimpleNamespace(
        protocol="protocol.yml", mode=None, transport="streamable-http", port=8080,
    ), protocol)
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.transport_security.allowed_hosts == [
        "localhost:8080", "127.0.0.1:8080", "[::1]:8080",
        "safe.tunnel.snodo.dev", "safe.tunnel.snodo.dev:8080",
    ]
    assert mcp.settings.transport_security.allowed_origins == [
        "http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080",
        "https://safe.tunnel.snodo.dev",
    ]
