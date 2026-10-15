"""Recovery behavior for a managed tunnel whose deletion failed."""

from types import SimpleNamespace

from snodo.cli.commands import serve_cmd, serve_tunnel_config


def test_failed_delete_blocks_restart_then_retry_clears_record(tmp_path, monkeypatch, capsys):
    config = {"hostname": "one.tunnel.snodo.dev", "tunnel_token": "secret", "port": 8000}
    monkeypatch.setattr(serve_cmd, "_deprovision_tunnel", lambda *_: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    assert serve_cmd._handle_tunnel_delete(str(tmp_path), config, "key") == 1
    stored = serve_tunnel_config._load_tunnel_config(str(tmp_path))
    assert stored["tunnel_token"] == "secret"
    assert stored["recovery"] == {"action": "delete", "hostname": "one.tunnel.snodo.dev"}
    assert "--delete --hostname one.tunnel.snodo.dev" in capsys.readouterr().err

    monkeypatch.setattr(serve_cmd, "_derive_project_root", lambda _: str(tmp_path))
    monkeypatch.setattr(serve_cmd, "_check_cloudflared", lambda: True)
    monkeypatch.setattr(serve_cmd, "_get_snodo_api_key", lambda: "key")
    monkeypatch.setattr(serve_cmd, "_load_tunnel_config", lambda _: stored)
    effects = []
    monkeypatch.setattr(serve_cmd, "_provision_tunnel", lambda *a: effects.append("provision"))
    monkeypatch.setattr(serve_cmd.subprocess, "Popen", lambda *a, **k: effects.append("process"))
    args = SimpleNamespace(protocol="protocol.yml", mode=None, transport="http", rotate=False,
                           delete=False, hostname=None, auth=None, port=None, verbose=False)
    assert serve_cmd._run_tunnel(args, SimpleNamespace(initial_mode="default"), "protocol.yml") == 1
    assert effects == []
    assert "refusing to reuse" in capsys.readouterr().err

    monkeypatch.setattr(serve_cmd, "_deprovision_tunnel", lambda *_: True)
    assert serve_cmd._handle_tunnel_delete(str(tmp_path), stored, "key", stored["hostname"]) == 0
    assert not (tmp_path / ".snodo" / "tunnel.json").exists()


def test_different_delete_target_preserves_local_record(tmp_path, monkeypatch):
    config = {"hostname": "kept.tunnel.snodo.dev", "tunnel_token": "secret"}
    serve_tunnel_config._save_tunnel_config(str(tmp_path), config)
    monkeypatch.setattr(serve_cmd, "_deprovision_tunnel", lambda *_: True)
    assert serve_cmd._handle_tunnel_delete(str(tmp_path), config, "key", "other.tunnel.snodo.dev") == 0
    assert serve_tunnel_config._load_tunnel_config(str(tmp_path))["tunnel_token"] == "secret"


def test_recovery_metadata_round_trips_without_secrets(tmp_path):
    serve_tunnel_config._save_tunnel_config(str(tmp_path), {
        "hostname": "one.tunnel.snodo.dev", "tunnel_token": "private",
        "recovery": {"action": "delete", "hostname": "one.tunnel.snodo.dev", "detail": "secret"},
    })
    loaded = serve_tunnel_config._load_tunnel_config(str(tmp_path))
    assert loaded["recovery"] == {"action": "delete", "hostname": "one.tunnel.snodo.dev"}
    assert "detail" not in loaded["recovery"]
