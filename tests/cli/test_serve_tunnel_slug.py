"""Tunnel hostnames are one DNS label, whatever the project is called (issue #308).

A project directory name may contain a dot, and a dot in a hostname is a
label separator, not a character: "droptrack.io" once provisioned
droptrack.io-all-<short>.tunnel.snodo.dev — a name one level deeper than
the wildcard certificate covers, so TLS could never match it. These tests
pin the fix: the slug sent for provisioning is a single valid DNS label,
distinct projects and distinct modes never collapse onto one hostname, and
a name that cannot be made servable is refused with a reason.

FILE: tests/cli/test_serve_tunnel_slug.py
"""

import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from snodo.cli.commands import serve_cmd

_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\Z")
_TUNNEL_DOMAIN = ".tunnel.snodo.dev"


def _leading_label(hostname: str) -> str:
    assert hostname.endswith(_TUNNEL_DOMAIN)
    return hostname[: -len(_TUNNEL_DOMAIN)]


def _cloud_hostname(slug: str, mode: str, short_id: str = "kfc93s") -> str:
    """The hostname the cloud builds from a provisioning request."""
    return f"{slug}-{mode}-{short_id}{_TUNNEL_DOMAIN}"


def _args(tmp_path, name, mode="all"):
    root = tmp_path / name
    (root / ".snodo").mkdir(parents=True)
    return SimpleNamespace(
        protocol=str(root / ".snodo" / "protocol.yml"),
        mode=mode, transport="streamable-http", port=55441,
        rotate=False, delete=False, hostname=None,
    )


class TestSlugIsSingleLabel:
    def test_dotted_directory_provisions_one_label_hostname(self, tmp_path):
        slug = serve_cmd._tunnel_project_slug(str(tmp_path / "droptrack.io"))
        hostname = _cloud_hostname(slug, "all")
        assert "." not in slug
        assert _LABEL.match(slug)
        assert _LABEL.match(_leading_label(hostname))
        assert hostname.count(".") == 3  # exactly <one label>.tunnel.snodo.dev
        assert serve_cmd._TUNNEL_HOSTNAME_RE.fullmatch(hostname)

    def test_run_tunnel_sends_a_single_label_slug(self, tmp_path):
        captured = {}

        def fake_provision(api_key, project_slug, mode, short_id, version, port=55441):
            captured["project_slug"] = project_slug
            captured["mode"] = mode
            raise serve_cmd.TunnelAPIError("stop after capture")

        with patch.object(serve_cmd, "_check_cloudflared", return_value=True), \
             patch.object(serve_cmd, "_get_snodo_api_key", return_value="key"), \
             patch.object(serve_cmd, "_load_tunnel_config", return_value={}), \
             patch.object(serve_cmd, "_provision_tunnel", side_effect=fake_provision):
            assert serve_cmd._run_tunnel(_args(tmp_path, "droptrack.io"), {}, "") == 1

        assert _LABEL.match(captured["project_slug"])

    def test_clean_directory_name_passes_through_unchanged(self, tmp_path):
        """Projects that already worked keep the hostname they have."""
        slug = serve_cmd._tunnel_project_slug(str(tmp_path / "droptrack"))
        assert slug == "droptrack"


class TestNoCollapse:
    def test_two_modes_of_one_project_produce_different_hostnames(self, tmp_path):
        slug = serve_cmd._tunnel_project_slug(str(tmp_path / "droptrack.io"))
        h1 = _cloud_hostname(slug, "all", "aaaaaa")
        h2 = _cloud_hostname(slug, "recon", "aaaaaa")
        assert h1 != h2
        assert serve_cmd._TUNNEL_HOSTNAME_RE.fullmatch(h1)
        assert serve_cmd._TUNNEL_HOSTNAME_RE.fullmatch(h2)

    def test_different_directories_do_not_collapse(self, tmp_path):
        names = ["droptrack.io", "droptrack-io", "droptrack.io-", "droptrackio"]
        slugs = {serve_cmd._tunnel_project_slug(str(tmp_path / n)) for n in names}
        assert len(slugs) == len(names)
        for slug in slugs:
            assert _LABEL.match(slug)

    def test_slug_is_stable_across_runs(self, tmp_path):
        root = str(tmp_path / "droptrack.io")
        assert serve_cmd._tunnel_project_slug(root) == serve_cmd._tunnel_project_slug(root)

    def test_slug_never_exceeds_dns_label_limit(self, tmp_path):
        long_name = ("droptrack-io-really-unreasonably-long-directory-name-"
                     "that-nobody-would-use-but-we-must-not-overrun-a-dns-label")
        slug = serve_cmd._tunnel_project_slug(str(tmp_path / long_name))
        assert len(slug) <= 63
        assert _LABEL.match(slug)


class TestRefusal:
    def test_unservable_name_is_refused_with_reason(self, tmp_path):
        with pytest.raises(serve_cmd.TunnelAPIError) as excinfo:
            serve_cmd._tunnel_project_slug(str(tmp_path / "___"))
        message = str(excinfo.value)
        assert "DNS label" in message
        assert "___" in message

    def test_run_tunnel_refuses_instead_of_provisioning(self, tmp_path, capsys):
        with patch.object(serve_cmd, "_check_cloudflared", return_value=True), \
             patch.object(serve_cmd, "_get_snodo_api_key", return_value="key"), \
             patch.object(serve_cmd, "_load_tunnel_config", return_value={}), \
             patch.object(serve_cmd, "_provision_tunnel") as provision:
            assert serve_cmd._run_tunnel(_args(tmp_path, "_._"), {}, "") == 1

        provision.assert_not_called()
        err = capsys.readouterr().err
        assert "DNS label" in err


class TestLegacyBrokenTunnelIsSurfaced:
    def test_broken_shape_hostname_is_reported_with_a_way_out(self, capsys):
        broken = f"droptrack.io-all-kfc93s{_TUNNEL_DOMAIN}"
        assert serve_cmd._warn_if_unservable_hostname(broken) is True
        err = capsys.readouterr().err
        assert broken in err
        assert "--delete --hostname" in err

    def test_servable_hostname_passes_quietly(self, capsys):
        assert serve_cmd._warn_if_unservable_hostname(
            f"droptrack-io-1a2b3c-all-kfc93s{_TUNNEL_DOMAIN}"
        ) is False
        assert capsys.readouterr().err == ""


class TestHostnameRegexShape:
    def test_regex_no_longer_tolerates_embedded_dots(self):
        broken = f"droptrack.io-all-kfc93s{_TUNNEL_DOMAIN}"
        assert not serve_cmd._TUNNEL_HOSTNAME_RE.fullmatch(broken)

    def test_regex_still_extracts_a_real_hostname_from_a_409_body(self):
        hostname = f"droptrack-io-1a2b3c-all-kfc93s{_TUNNEL_DOMAIN}"
        body = f'conflict: slot taken by {hostname}'
        assert serve_cmd._extract_existing_hostname(body) == hostname
