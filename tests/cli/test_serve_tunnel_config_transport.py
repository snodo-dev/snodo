"""Transport state and option validation for managed serve tunnels."""

import json

import pytest

from snodo.cli.commands import serve_tunnel_config


def test_websocket_tunnel_state_round_trips_without_secrets(tmp_path):
    serve_tunnel_config._save_tunnel_config(str(tmp_path), {
        "hostname": "ws.tunnel.snodo.dev", "transport": "ws",
        "machine_id": "machine-123", "client_secret": "must-not-persist",
    })

    loaded = serve_tunnel_config._load_tunnel_config(str(tmp_path))

    assert loaded["hostname"] == "ws.tunnel.snodo.dev"
    assert loaded["transport"] == "ws"
    assert loaded["machine_id"] == "machine-123"
    assert "client_secret" not in loaded
    assert "must-not-persist" not in (tmp_path / ".snodo" / "tunnel.json").read_text()


def test_legacy_cloudflare_state_defaults_transport(tmp_path):
    path = tmp_path / ".snodo"
    path.mkdir()
    (path / "tunnel.json").write_text(json.dumps({
        "hostname": "old.tunnel.snodo.dev", "tunnel_token": "old",
    }))

    loaded = serve_tunnel_config._load_tunnel_config(str(tmp_path))

    assert loaded == {
        "hostname": "old.tunnel.snodo.dev", "tunnel_token": "old",
        "transport": "cloudflare",
    }


@pytest.mark.parametrize(("config", "expected"), [
    ({"transport": "ws", "hostname": "ws.tunnel.snodo.dev"}, True),
    ({"transport": "ws", "hostname": ""}, False),
    ({"transport": "cloudflare", "hostname": "cf.tunnel.snodo.dev"}, False),
    ({"hostname": "cf.tunnel.snodo.dev", "tunnel_token": "token"}, True),
])
def test_has_usable_saved_tunnel(config, expected):
    assert serve_tunnel_config._has_usable_saved_tunnel(config) is expected


def test_tunnel_transport_defaults_and_accepts_values():
    assert serve_tunnel_config._tunnel_transport(None) == "cloudflare"
    assert serve_tunnel_config._tunnel_transport("cloudflare") == "cloudflare"
    assert serve_tunnel_config._tunnel_transport("ws") == "ws"


def test_invalid_tunnel_transport_names_allowed_values():
    with pytest.raises(ValueError, match="cloudflare or ws"):
        serve_tunnel_config._tunnel_transport("ssh")


def test_ws_auth_validation_allows_oauth_and_rejects_service_token():
    assert serve_tunnel_config._validate_tunnel_auth("ws", None) == ["oauth"]
    assert serve_tunnel_config._validate_tunnel_auth("ws", ["oauth"]) == ["oauth"]
    with pytest.raises(ValueError, match="service-token.*not supported over ws yet"):
        serve_tunnel_config._validate_tunnel_auth("ws", ["service-token"])


def test_ws_public_tunnel_metadata_round_trips(tmp_path):
    config = {
        "transport": "ws", "hostname": "private.snodo.dev",
        "org_id": "org-123", "public_hostname": "random-pub.snodo.dev",
        "last_printed_public_hostname": "previous-pub.snodo.dev",
    }

    serve_tunnel_config._save_tunnel_config(str(tmp_path), config)

    loaded = serve_tunnel_config._load_tunnel_config(str(tmp_path))
    assert {key: loaded[key] for key in (
        "org_id", "public_hostname", "last_printed_public_hostname",
    )} == {key: config[key] for key in (
        "org_id", "public_hostname", "last_printed_public_hostname",
    )}


def test_save_without_public_metadata_does_not_invent_it(tmp_path):
    serve_tunnel_config._save_tunnel_config(str(tmp_path), {
        "transport": "cloudflare", "hostname": "cf.snodo.dev",
    })

    loaded = serve_tunnel_config._load_tunnel_config(str(tmp_path))
    assert not {"org_id", "public_hostname", "last_printed_public_hostname"} & loaded.keys()


def test_save_keeps_existing_public_alias_unless_explicitly_cleared(tmp_path):
    serve_tunnel_config._save_tunnel_config(str(tmp_path), {
        "transport": "ws", "hostname": "private.snodo.dev",
        "public_hostname": "random-pub.snodo.dev",
    })
    serve_tunnel_config._save_tunnel_config(str(tmp_path), {
        "transport": "ws", "hostname": "private.snodo.dev",
    })
    assert serve_tunnel_config._load_tunnel_config(str(tmp_path))["public_hostname"] == "random-pub.snodo.dev"

    serve_tunnel_config._save_tunnel_config(str(tmp_path), {
        "transport": "ws", "hostname": "private.snodo.dev", "public_hostname": None,
    })
    assert serve_tunnel_config._load_tunnel_config(str(tmp_path))["public_hostname"] is None
