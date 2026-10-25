from mcp.server.transport_security import TransportSecuritySettings

from snodo.cli.commands.serve_security import build_transport_security_settings


def test_loopback_transport_security_settings() -> None:
    settings = build_transport_security_settings(8080, None)

    assert isinstance(settings, TransportSecuritySettings)
    assert settings.allowed_hosts == ["localhost:8080", "127.0.0.1:8080", "[::1]:8080"]
    assert settings.allowed_origins == [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
    ]
    assert settings.enable_dns_rebinding_protection is True


def test_tunnel_transport_security_settings() -> None:
    settings = build_transport_security_settings(8080, "example.tunnel.snodo.dev")

    assert isinstance(settings, TransportSecuritySettings)
    assert settings.allowed_hosts == [
        "localhost:8080",
        "127.0.0.1:8080",
        "[::1]:8080",
        "example.tunnel.snodo.dev",
        "example.tunnel.snodo.dev:8080",
    ]
    assert settings.allowed_origins == [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
        "https://example.tunnel.snodo.dev",
    ]
    assert settings.enable_dns_rebinding_protection is True
