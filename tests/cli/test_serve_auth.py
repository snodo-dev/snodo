"""Authentication choices for managed serve tunnels."""

import re
from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from snodo.cli.commands import serve_cmd


def test_auth_defaults_to_oauth_and_refuses_service_tokens():
    assert serve_cmd._auth_methods(None) == ["oauth"]
    try:
        serve_cmd._auth_methods(["service-token"])
    except ValueError as error:
        assert "OAuth" in str(error)
        assert "mcp-auth.snodo.dev" in str(error)
    else:  # pragma: no cover
        raise AssertionError("service-token auth was accepted")


def test_invalid_auth_value_is_named():
    try:
        serve_cmd._auth_methods(["client-credentials"])
    except ValueError as error:
        assert "client-credentials" in str(error)
    else:  # pragma: no cover
        raise AssertionError("invalid auth value was accepted")


def test_provision_request_does_not_send_retired_auth_set():
    response = MagicMock(status_code=200)
    response.json.return_value = {"hostname": "x.tunnel.snodo.dev", "tunnel_token": "t"}
    with patch.object(serve_cmd, "_get_cloud_tunnel_api_url", return_value="https://cloud"), \
         patch("httpx.post", return_value=response) as post:
        serve_cmd._provision_tunnel("key", "project", "all", "short", "1")

    assert "auth" not in post.call_args.kwargs["json"]


def test_legacy_service_token_auth_cannot_disable_oauth(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: "/isolated")
    monkeypatch.setattr(serve_cmd, "_check_cloudflared", lambda: True)
    monkeypatch.setattr(serve_cmd, "_get_snodo_api_key", lambda: "key")
    monkeypatch.setattr(serve_cmd, "_load_tunnel_config", lambda _: {
        "hostname": "legacy.tunnel.snodo.dev", "tunnel_token": "token",
        "auth": ["service-token"],
    })
    monkeypatch.setattr(serve_cmd, "_choose_serve_port", lambda _: 8000)
    monkeypatch.setattr(serve_cmd, "_wait_for_server_bind", lambda _: False)
    process = MagicMock(returncode=1, stderr=None)
    commands = []
    monkeypatch.setattr(serve_cmd.subprocess, "Popen", lambda *a, **k: commands.append(a[0]) or process)
    monkeypatch.setattr(serve_cmd, "_terminate_process_group", lambda _: None)
    args = SimpleNamespace(protocol="protocol.yml", mode=None, transport="http", rotate=False,
                           delete=False, hostname=None, auth=None, port=8000, verbose=False)
    assert serve_cmd._run_tunnel(args, SimpleNamespace(initial_mode="default"), "protocol.yml") == 1
    assert commands and commands[0][commands[0].index("--auth") + 1] == "oauth"


def test_explicit_service_token_rejected_before_external_effects(monkeypatch, capsys):
    from types import SimpleNamespace

    def forbidden(*_a, **_k):
        raise AssertionError("side effect occurred")

    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: "/isolated")
    monkeypatch.setattr(serve_cmd, "_check_cloudflared", forbidden)
    args = SimpleNamespace(protocol="protocol.yml", mode=None, transport="http", rotate=False,
                           delete=False, hostname=None, auth=["service-token"], port=None, verbose=False)
    assert serve_cmd._run_tunnel(args, SimpleNamespace(initial_mode="default"), "protocol.yml") == 1
    assert "OAuth" in capsys.readouterr().err


def test_banner_only_shows_url_and_oauth_server(capsys):
    serve_cmd._print_first_run_info(
        "x.tunnel.snodo.dev",
    )
    output = capsys.readouterr().out
    assert "https://x.tunnel.snodo.dev/mcp" in output
    # codeql[py/incomplete-url-substring-sanitization] false positive: this
    # asserts the banner prints the literal OAuth server URL; no URL is parsed.
    assert "https://mcp-auth.snodo.dev" in output
    assert "CF-Access-Client" not in output


def test_help_names_values_and_any_semantics():
    app = typer.Typer()
    serve_cmd.register(app)
    result = CliRunner().invoke(app, ["serve", "--help"])
    # Rich colours help output when it detects CI (GITHUB_ACTIONS) and styles
    # "-" and "-verbose" separately, so match on the text without ANSI codes.
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)

    assert result.exit_code == 0
    assert "OAuth" in out
    assert "service-token" not in out
    assert "--delete" in out and "--tunnel" in out
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


def test_legacy_service_token_tunnel_enables_oauth(monkeypatch):
    from types import SimpleNamespace

    server = MagicMock()
    server.get_tools.return_value = []
    mcp = MagicMock()
    mcp.settings = SimpleNamespace(transport_security=SimpleNamespace(), port=None)
    protocol = SimpleNamespace(protocol_id="test", get_mode=lambda _: True)
    monkeypatch.setattr("snodo.mcp.server.ProtocolMCPServer", lambda **_: server)
    monkeypatch.setattr("snodo.mcp.transport.build_fastmcp_server", lambda *_a, **kwargs: (mcp, kwargs)[0])
    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: "/project")
    monkeypatch.setattr(serve_cmd, "_load_tunnel_config", lambda _: {
        "hostname": "legacy.tunnel.snodo.dev", "auth": ["service-token"],
    })
    monkeypatch.setattr(serve_cmd, "_choose_serve_port", lambda _: 8080)
    monkeypatch.setattr("snodo.infrastructure.jwks.JwksClient.fetch", lambda _: True)
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.install", lambda: None)
    monkeypatch.setattr("snodo.infrastructure.cloud_liveness.uninstall", lambda: None)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "")
    seen = {}
    def build(_server, **kwargs):
        seen.update(kwargs)
        return mcp
    monkeypatch.setattr("snodo.mcp.transport.build_fastmcp_server", build)
    serve_cmd._run_server(SimpleNamespace(
        protocol="protocol.yml", mode=None, transport="streamable-http", port=8080,
    ), protocol)
    assert "token_verifier" in seen


def test_service_token_request_fails_before_provisioning(monkeypatch, capsys):
    from types import SimpleNamespace

    import pytest

    monkeypatch.setattr(serve_cmd, "load_protocol", lambda _: object())
    monkeypatch.setattr(serve_cmd, "_run_tunnel", lambda *_: pytest.fail("tunnel ran"))
    args = SimpleNamespace(
        auth=["service-token"], protocol="unused.yml", tunnel=True,
        mcp_install=False, mcp_uninstall=False, mcp_uninstall_all=False,
        mcp_list=False, install=False, uninstall=False, uninstall_all=False,
    )
    assert serve_cmd.serve_command(args) == 2
    message = capsys.readouterr().err
    assert "OAuth" in message and "mcp-auth.snodo.dev" in message
