"""Pin tunnel provisioning parameters for WebSocket tunnels (Fixes #1029)."""

from types import SimpleNamespace
from unittest.mock import patch

from snodo.cli.commands import serve_cmd


def _provision_call(tunnel_transport, monkeypatch, *, public_url=False, saved=None, response=None):
    protocol = SimpleNamespace(initial_mode="all")
    args = SimpleNamespace(
        protocol="/tmp/project/.snodo/protocol.yml",
        mode=None,
        transport="streamable-http",
        tunnel_transport=tunnel_transport,
        auth=None,
        port=8000,
        delete=False,
        rotate=False,
        verbose=False,
        public_url=public_url,
    )
    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: "/tmp/project")
    monkeypatch.setattr(serve_cmd, "_get_snodo_api_key", lambda: "key")
    monkeypatch.setattr(serve_cmd, "_load_tunnel_config", lambda _: saved or {})
    monkeypatch.setattr(serve_cmd, "_choose_serve_port", lambda port: port)
    saved_configs = []
    monkeypatch.setattr(serve_cmd, "_save_tunnel_config", lambda _, config: saved_configs.append(config.copy()))
    monkeypatch.setattr(serve_cmd, "_wait_for_server_bind", lambda _: False)
    monkeypatch.setattr(serve_cmd, "_terminate_process_group", lambda *_: None)
    monkeypatch.setattr(serve_cmd, "_check_cloudflared", lambda: True)
    import importlib
    importlib.import_module("snodo.infrastructure.cloud_relay")

    with patch("snodo.infrastructure.cloud_relay.machine_id", return_value="machine-123"), \
         patch.object(serve_cmd, "_provision_tunnel", return_value=response or {"hostname": "project-ws.snodo.dev"}) as provision, \
         patch.object(serve_cmd.subprocess, "Popen", lambda *_, **__: SimpleNamespace(stderr=None, returncode=1)):
        serve_cmd._run_tunnel(args, protocol, args.protocol)
    provision.saved_configs = saved_configs
    return provision


def test_ws_without_auth_provisions_with_transport_and_machine_id(monkeypatch):
    provision = _provision_call("ws", monkeypatch)

    assert provision.call_args.kwargs["tunnel_transport"] == "ws"
    assert provision.call_args.kwargs["machine_identifier"] == "machine-123"


def test_cloudflare_without_auth_keeps_legacy_provision_call(monkeypatch):
    provision = _provision_call("cloudflare", monkeypatch)

    assert "tunnel_transport" not in provision.call_args.kwargs
    assert "machine_identifier" not in provision.call_args.kwargs


def test_ws_provision_request_includes_transport_and_machine_id(monkeypatch):
    import httpx

    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs["json"]
        response = SimpleNamespace(status_code=200, json=lambda: {"hostname": "project-ws.snodo.dev"})
        return response

    monkeypatch.setattr(serve_cmd, "_get_cloud_tunnel_api_url", lambda: "https://worker.example")
    monkeypatch.setattr(httpx, "post", fake_post)

    serve_cmd._provision_tunnel(
        "key", "project", "all", "abc123", "1.2.3", 8000,
        tunnel_transport="ws", machine_identifier="machine-123",
    )

    assert captured["json"]["transport"] == "ws"
    assert captured["json"]["machine_id"] == "machine-123"


def test_ws_public_url_is_sent_when_requested(monkeypatch):
    import httpx

    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs["json"]
        return SimpleNamespace(status_code=200, json=lambda: {
            "hostname": "project-ws.snodo.dev",
            "org_id": "org-123",
            "public_hostname": "random-pub.snodo.dev",
        })

    monkeypatch.setattr(serve_cmd, "_get_cloud_tunnel_api_url", lambda: "https://worker.example")
    monkeypatch.setattr(httpx, "post", fake_post)

    serve_cmd._provision_tunnel(
        "key", "project", "all", "abc123", "1.2.3", 8000,
        tunnel_transport="ws", machine_identifier="machine-123", public_url=True,
    )

    assert captured["json"]["public_url"] is True


def test_ws_provision_request_omits_public_url_without_flag(monkeypatch):
    import httpx

    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs["json"]
        return SimpleNamespace(status_code=200, json=lambda: {"hostname": "project-ws.snodo.dev"})

    monkeypatch.setattr(serve_cmd, "_get_cloud_tunnel_api_url", lambda: "https://worker.example")
    monkeypatch.setattr(httpx, "post", fake_post)

    serve_cmd._provision_tunnel(
        "key", "project", "all", "abc123", "1.2.3", 8000,
        tunnel_transport="ws", machine_identifier="machine-123",
    )

    assert "public_url" not in captured["json"]


def test_public_url_is_refused_for_cloudflare_and_non_tunnel(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(serve_cmd, "load_protocol", lambda _: {"loaded": True})
    for tunnel, tunnel_transport in ((True, "cloudflare"), (False, "ws")):
        args = SimpleNamespace(
            protocol="protocol.yml", mode=None, transport="stdio", port=None,
            tunnel=tunnel, tunnel_transport=tunnel_transport, public_url=True,
            auth=None,
        )
        assert serve_cmd.serve_command(args) == 2
        assert "--public-url requires --tunnel --tunnel-transport ws" in capsys.readouterr().err


def test_saved_ws_tunnel_is_provisioned_again_and_metadata_saved(monkeypatch):
    saved = {"hostname": "old-ws.snodo.dev", "transport": "ws", "port": 8000}
    response = {
        "hostname": "project-ws.snodo.dev", "org_id": "org-123",
        "public_hostname": "random-pub.snodo.dev",
    }
    provision = _provision_call("ws", monkeypatch, public_url=True, saved=saved, response=response)

    assert provision.call_count == 1
    assert provision.call_args.kwargs["public_url"] is True
    assert provision.saved_configs[0]["org_id"] == "org-123"
    assert provision.saved_configs[0]["public_hostname"] == "random-pub.snodo.dev"


def test_public_url_missing_provision_metadata_fails_before_start(monkeypatch, capsys):
    for response, missing in (({"hostname": "project-ws.snodo.dev", "public_hostname": "pub.snodo.dev"}, "org_id"),
                              ({"hostname": "project-ws.snodo.dev", "org_id": "org-123"}, "public_hostname")):
        provision = _provision_call("ws", monkeypatch, public_url=True, response=response)
        assert provision.call_count == 1
        assert missing in capsys.readouterr().err
