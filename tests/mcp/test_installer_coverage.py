"""Characterization tests for missing installer.py branches.

Covers lines:
  81        get_claude_config_path: Windows APPDATA not set → RuntimeError
  167, 216, 260  install/uninstall/uninstall_all auto-detect config path
  347-374   purge_project_state
  386-411   scan_orphans
  423-442   remove_orphans
"""

import json
import os
from unittest.mock import patch

import pytest

from snodo.compiler.models import Protocol
from snodo.mcp.installer import (
    get_claude_config_path,
    install,
    uninstall,
    uninstall_all,
    purge_project_state,
    scan_orphans,
    remove_orphans,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_PROTOCOL_DATA = {
    "protocol_id": "test",
    "name": "Test Protocol",
    "version": "1.0.0",
    "modes": [
        {"mode_id": "producer", "name": "Producer", "tools": ["edit"], "validators": ["sec"]},
        {"mode_id": "reviewer", "name": "Reviewer", "tools": ["review"], "validators": ["sec"]},
    ],
    "validators": [{"validator_id": "sec", "validator_type": "security"}],
    "initial_mode": "producer",
}


@pytest.fixture
def protocol():
    return Protocol(**MINIMAL_PROTOCOL_DATA)


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "claude_desktop_config.json"


# ---------------------------------------------------------------------------
# get_claude_config_path: Windows APPDATA not set (line 81)
# ---------------------------------------------------------------------------

class TestGetClaudeConfigPath:
    def test_windows_appdata_nonexistent_raises(self):
        """Line 81: APPDATA points to non-existent path → RuntimeError."""
        with patch("platform.system", return_value="Windows"), \
             patch.dict(os.environ, {"APPDATA": "/no/such/appdata/path/xyz123"}, clear=False):
            with pytest.raises(RuntimeError, match="APPDATA environment variable not set"):
                get_claude_config_path()

    def test_unsupported_platform_raises(self):
        with patch("platform.system", return_value="FreeBSD"):
            with pytest.raises(RuntimeError, match="Unsupported platform"):
                get_claude_config_path()

    def test_darwin_returns_path(self):
        with patch("platform.system", return_value="Darwin"):
            p = get_claude_config_path()
        assert "Claude" in str(p)
        assert p.name == "claude_desktop_config.json"

    def test_linux_returns_path(self):
        with patch("platform.system", return_value="Linux"):
            p = get_claude_config_path()
        assert "Claude" in str(p)


# ---------------------------------------------------------------------------
# install / uninstall / uninstall_all: auto-detect config path (167, 216, 260)
# ---------------------------------------------------------------------------

class TestAutoDetectConfigPath:
    def test_install_uses_auto_detected_path(self, protocol, tmp_path):
        """Line 167: config_path=None → get_claude_config_path() called."""
        fake_cfg = tmp_path / "fake_config.json"
        with patch("snodo.mcp.installer.get_claude_config_path", return_value=fake_cfg):
            added, updated = install(
                protocol,
                protocol_path=str(tmp_path / "protocol.yml"),
                project_name="myproject",
                config_path=None,
            )
        assert fake_cfg.exists()
        assert len(added) == 2  # producer + reviewer

    def test_uninstall_uses_auto_detected_path(self, protocol, tmp_path):
        """Line 216: config_path=None in uninstall → get_claude_config_path()."""
        fake_cfg = tmp_path / "fake_config.json"
        # Pre-populate with entries
        existing = {
            "mcpServers": {
                "snodo-myproject-producer": {
                    "command": "snodo",
                    "args": ["serve", "--protocol", "/p/protocol.yml", "--mode", "producer"],
                }
            }
        }
        fake_cfg.write_text(json.dumps(existing))
        with patch("snodo.mcp.installer.get_claude_config_path", return_value=fake_cfg):
            removed = uninstall(
                protocol,
                protocol_path="/p/protocol.yml",
                project_name="myproject",
                config_path=None,
            )
        assert "snodo-myproject-producer" in removed

    def test_uninstall_all_uses_auto_detected_path(self, tmp_path):
        """Line 260: config_path=None in uninstall_all → get_claude_config_path()."""
        fake_cfg = tmp_path / "fake_config.json"
        existing = {
            "mcpServers": {
                "snodo-proj-mode1": {"command": "snodo", "args": []},
                "other-tool": {"command": "other", "args": []},
            }
        }
        fake_cfg.write_text(json.dumps(existing))
        with patch("snodo.mcp.installer.get_claude_config_path", return_value=fake_cfg):
            removed = uninstall_all(config_path=None)
        assert "snodo-proj-mode1" in removed
        assert "other-tool" not in removed


# ---------------------------------------------------------------------------
# purge_project_state (lines 347-374)
# ---------------------------------------------------------------------------

class TestPurgeProjectState:
    def test_purges_snodo_dir(self, tmp_path):
        """snodo_dir removed and reported in purged_paths."""
        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "state.json").write_text("{}")
        result = purge_project_state(str(tmp_path))
        assert not snodo_dir.exists()
        assert str(snodo_dir) in result["purged_paths"]

    def test_no_snodo_dir_no_paths(self, tmp_path):
        result = purge_project_state(str(tmp_path))
        assert result["purged_paths"] == []
        assert result["session_count"] == 0

    def test_removes_matching_sessions(self, tmp_path):
        """Session files with matching project_id are removed."""
        import hashlib
        project_id = hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()[:16]

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # Matching session
        matching = sessions_dir / "sess_match.json"
        matching.write_text(json.dumps({"project_id": project_id, "other": "data"}))
        # Non-matching session
        non_matching = sessions_dir / "sess_other.json"
        non_matching.write_text(json.dumps({"project_id": "other_id"}))

        with patch("snodo.infrastructure.paths.resolve_home", return_value=tmp_path):
            result = purge_project_state(str(tmp_path))
        assert result["session_count"] == 1
        assert not matching.exists()
        assert non_matching.exists()

    def test_corrupt_session_file_skipped(self, tmp_path):
        """Corrupt session JSON files are skipped without crashing."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "bad.json").write_text("{{invalid json}")

        with patch("snodo.infrastructure.paths.resolve_home", return_value=tmp_path):
            result = purge_project_state(str(tmp_path))
        assert result["session_count"] == 0

    def test_resolve_home_exception_swallowed(self, tmp_path):
        """Exception in session cleanup is swallowed (pass block)."""
        with patch("snodo.infrastructure.paths.resolve_home", side_effect=RuntimeError("no home")):
            result = purge_project_state(str(tmp_path))
        assert "purged_paths" in result
        assert result["session_count"] == 0


# ---------------------------------------------------------------------------
# scan_orphans (lines 386-411)
# ---------------------------------------------------------------------------

class TestScanOrphans:
    def test_empty_config_returns_empty(self, config_path):
        config_path.write_text(json.dumps({}))
        assert scan_orphans(config_path) == []

    def test_missing_protocol_file_is_orphan(self, config_path, tmp_path):
        """snodo-* entry whose --protocol file doesn't exist → orphan."""
        config = {
            "mcpServers": {
                "snodo-proj-producer": {
                    "command": "snodo",
                    "args": [
                        "serve",
                        "--protocol",
                        str(tmp_path / "missing_protocol.yml"),
                        "--mode",
                        "producer",
                    ],
                }
            }
        }
        config_path.write_text(json.dumps(config))
        orphans = scan_orphans(config_path)
        assert len(orphans) == 1
        assert orphans[0]["entry_name"] == "snodo-proj-producer"
        assert "missing_protocol.yml" in orphans[0]["missing_path"]

    def test_existing_protocol_not_orphan(self, config_path, tmp_path):
        proto_path = tmp_path / "protocol.yml"
        proto_path.write_text("protocol_id: test")
        config = {
            "mcpServers": {
                "snodo-proj-producer": {
                    "command": "snodo",
                    "args": ["serve", "--protocol", str(proto_path), "--mode", "producer"],
                }
            }
        }
        config_path.write_text(json.dumps(config))
        orphans = scan_orphans(config_path)
        assert orphans == []

    def test_non_snodo_entry_skipped(self, config_path, tmp_path):
        """Non-snodo-* entries are not checked."""
        config = {
            "mcpServers": {
                "other-tool": {
                    "command": "other",
                    "args": ["--protocol", str(tmp_path / "missing.yml")],
                }
            }
        }
        config_path.write_text(json.dumps(config))
        assert scan_orphans(config_path) == []

    def test_no_protocol_flag_in_args_skipped(self, config_path):
        """Entry without --protocol in args doesn't crash."""
        config = {
            "mcpServers": {
                "snodo-proj-mode": {"command": "snodo", "args": ["serve"]}
            }
        }
        config_path.write_text(json.dumps(config))
        # No protocol path found → orphan not reported (path is None)
        assert scan_orphans(config_path) == []

    def test_auto_detect_config_path(self, tmp_path):
        """scan_orphans with config_path=None calls get_claude_config_path."""
        fake_cfg = tmp_path / "cfg.json"
        fake_cfg.write_text("{}")
        with patch("snodo.mcp.installer.get_claude_config_path", return_value=fake_cfg):
            result = scan_orphans(config_path=None)
        assert result == []

    def test_args_not_iterable_skipped(self, config_path):
        """Lines 403-404: non-iterable args value → TypeError caught, entry skipped."""
        config = {
            "mcpServers": {
                "snodo-proj-broken": {
                    "command": "snodo",
                    "args": 42,  # not a list → TypeError on enumerate
                }
            }
        }
        config_path.write_text(json.dumps(config))
        # Should not raise; just yields no orphans (no protocol_path extracted)
        result = scan_orphans(config_path)
        assert result == []


# ---------------------------------------------------------------------------
# remove_orphans (lines 423-442)
# ---------------------------------------------------------------------------

class TestRemoveOrphans:
    def test_no_orphans_returns_empty(self, config_path, tmp_path):
        proto_path = tmp_path / "existing.yml"
        proto_path.write_text("protocol_id: test")
        config = {
            "mcpServers": {
                "snodo-proj-mode": {
                    "command": "snodo",
                    "args": ["serve", "--protocol", str(proto_path), "--mode", "m"],
                }
            }
        }
        config_path.write_text(json.dumps(config))
        removed = remove_orphans(config_path)
        assert removed == []

    def test_removes_orphan_entries(self, config_path, tmp_path):
        """Orphan entries removed, non-orphan preserved."""
        proto_existing = tmp_path / "real.yml"
        proto_existing.write_text("protocol_id: real")
        proto_missing = str(tmp_path / "gone.yml")

        config = {
            "mcpServers": {
                "snodo-proj-orphan": {
                    "command": "snodo",
                    "args": ["serve", "--protocol", proto_missing, "--mode", "m"],
                },
                "snodo-proj-real": {
                    "command": "snodo",
                    "args": ["serve", "--protocol", str(proto_existing), "--mode", "m"],
                },
                "other-unrelated": {"command": "other", "args": []},
            }
        }
        config_path.write_text(json.dumps(config))
        removed = remove_orphans(config_path)
        assert removed == ["snodo-proj-orphan"]

        # Verify config was rewritten correctly
        saved = json.loads(config_path.read_text())
        assert "snodo-proj-orphan" not in saved["mcpServers"]
        assert "snodo-proj-real" in saved["mcpServers"]
        assert "other-unrelated" in saved["mcpServers"]

    def test_auto_detect_config_path(self, tmp_path):
        """remove_orphans with config_path=None uses auto-detected path."""
        fake_cfg = tmp_path / "cfg.json"
        fake_cfg.write_text("{}")
        with patch("snodo.mcp.installer.get_claude_config_path", return_value=fake_cfg):
            removed = remove_orphans(config_path=None)
        assert removed == []
