"""Tests for Claude Desktop MCP Installer (Tasks 3.8-3.11).

FILE: tests/mcp/test_installer.py

Tests: project name derivation, OS detection, config path resolution,
entry generation, config read/write, merging, uninstall, CLI integration.
"""

import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from snodo.compiler.models import Protocol
from snodo.mcp.installer import (
    sanitize_project_name,
    derive_project_name,
    get_claude_config_path,
    generate_mcp_entries,
    read_claude_config,
    write_claude_config,
    install,
    uninstall,
    uninstall_all,
    print_install_result,
    print_uninstall_result,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_PROTOCOL_DATA = {
    "protocol_id": "test",
    "name": "Test Protocol",
    "version": "1.0.0",
    "modes": [
        {
            "mode_id": "producer",
            "name": "Producer",
            "tools": ["edit"],
            "validators": ["security"],
        },
        {
            "mode_id": "reviewer",
            "name": "Reviewer",
            "tools": ["review"],
            "validators": ["security"],
        },
    ],
    "validators": [
        {
            "validator_id": "security",
            "validator_type": "security",
            "criteria": ["Check"],
        }
    ],
    "disagreement_policy": "unanimous",
    "initial_mode": "producer",
}


@pytest.fixture
def protocol():
    return Protocol(**MINIMAL_PROTOCOL_DATA)


@pytest.fixture
def single_mode_protocol():
    data = MINIMAL_PROTOCOL_DATA.copy()
    data["modes"] = [MINIMAL_PROTOCOL_DATA["modes"][0]]
    return Protocol(**data)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def config_path(temp_dir):
    return temp_dir / "Claude" / "claude_desktop_config.json"


# ---------------------------------------------------------------------------
# sanitize_project_name
# ---------------------------------------------------------------------------


class TestSanitizeProjectName:
    def test_lowercase(self):
        assert sanitize_project_name("MyApp") == "myapp"

    def test_dashes_to_underscores(self):
        assert sanitize_project_name("my-app") == "my_app"

    def test_spaces_to_underscores(self):
        assert sanitize_project_name("my app") == "my_app"

    def test_dots_to_underscores(self):
        assert sanitize_project_name("my.app") == "my_app"

    def test_strips_special_chars(self):
        assert sanitize_project_name("my@app!") == "myapp"

    def test_strips_leading_trailing_underscores(self):
        assert sanitize_project_name("-my-app-") == "my_app"

    def test_empty_returns_project(self):
        assert sanitize_project_name("") == "project"

    def test_only_special_chars_returns_project(self):
        assert sanitize_project_name("@#$") == "project"

    def test_mixed_separators(self):
        assert sanitize_project_name("My-Cool.App Name") == "my_cool_app_name"

    def test_already_clean(self):
        assert sanitize_project_name("myapp") == "myapp"

    def test_numbers(self):
        assert sanitize_project_name("app2") == "app2"

    def test_snodo_dev(self):
        assert sanitize_project_name("snodo-dev") == "snodo_dev"


# ---------------------------------------------------------------------------
# derive_project_name
# ---------------------------------------------------------------------------


class TestDeriveProjectName:
    def test_standard_layout(self, temp_dir):
        proto = temp_dir / ".snodo" / "protocol.yml"
        proto.parent.mkdir()
        proto.touch()
        name = derive_project_name(str(proto))
        assert name == sanitize_project_name(temp_dir.name)

    def test_custom_protocol_path(self, temp_dir):
        proto = temp_dir / "custom.yml"
        proto.touch()
        name = derive_project_name(str(proto))
        assert name == sanitize_project_name(temp_dir.name)

    def test_nested_snodo(self, temp_dir):
        project = temp_dir / "My-Project"
        project.mkdir()
        proto = project / ".snodo" / "protocol.yml"
        proto.parent.mkdir()
        proto.touch()
        name = derive_project_name(str(proto))
        assert name == "my_project"


# ---------------------------------------------------------------------------
# get_claude_config_path
# ---------------------------------------------------------------------------


class TestGetClaudeConfigPath:
    def test_macos_path(self):
        with patch("snodo.mcp.installer.platform.system", return_value="Darwin"):
            path = get_claude_config_path()
        assert path == Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"

    def test_linux_path(self):
        with patch("snodo.mcp.installer.platform.system", return_value="Linux"):
            path = get_claude_config_path()
        assert path == Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

    def test_windows_path(self):
        fake_appdata = "/tmp/fake_appdata"
        Path(fake_appdata).mkdir(parents=True, exist_ok=True)
        with patch("snodo.mcp.installer.platform.system", return_value="Windows"), \
             patch.dict("os.environ", {"APPDATA": fake_appdata}):
            path = get_claude_config_path()
        assert path == Path(fake_appdata) / "Claude" / "claude_desktop_config.json"

    def test_unsupported_os_raises(self):
        with patch("snodo.mcp.installer.platform.system", return_value="FreeBSD"):
            with pytest.raises(RuntimeError, match="Unsupported platform"):
                get_claude_config_path()


# ---------------------------------------------------------------------------
# generate_mcp_entries
# ---------------------------------------------------------------------------


class TestGenerateMCPEntries:
    def test_generates_entry_per_mode(self, protocol):
        entries = generate_mcp_entries(protocol, "/project/.snodo/protocol.yml", "myapp")
        assert len(entries) == 2
        assert "snodo-myapp-producer" in entries
        assert "snodo-myapp-reviewer" in entries

    def test_entry_structure(self, protocol):
        entries = generate_mcp_entries(protocol, "/project/.snodo/protocol.yml", "myapp")
        entry = entries["snodo-myapp-producer"]
        assert entry["command"] == "snodo"
        assert entry["args"] == [
            "serve", "--protocol", "/project/.snodo/protocol.yml",
            "--mode", "producer",
        ]

    def test_entry_uses_protocol_path(self, protocol):
        entries = generate_mcp_entries(protocol, "/other/path/proto.yml", "myapp")
        entry = entries["snodo-myapp-producer"]
        assert "/other/path/proto.yml" in entry["args"]

    def test_single_mode_protocol(self, single_mode_protocol):
        entries = generate_mcp_entries(single_mode_protocol, "/p.yml", "myapp")
        assert len(entries) == 1
        assert "snodo-myapp-producer" in entries

    def test_entry_names_use_project_prefix(self, protocol):
        entries = generate_mcp_entries(protocol, "/p.yml", "cool_project")
        for name in entries:
            assert name.startswith("snodo-cool_project-")

    def test_reviewer_entry_args(self, protocol):
        entries = generate_mcp_entries(protocol, "/project/protocol.yml", "myapp")
        entry = entries["snodo-myapp-reviewer"]
        assert entry["args"] == [
            "serve", "--protocol", "/project/protocol.yml",
            "--mode", "reviewer",
        ]

    def test_different_projects_dont_collide(self, protocol):
        entries_a = generate_mcp_entries(protocol, "/a/proto.yml", "app_a")
        entries_b = generate_mcp_entries(protocol, "/b/proto.yml", "app_b")
        assert set(entries_a.keys()).isdisjoint(set(entries_b.keys()))


# ---------------------------------------------------------------------------
# read_claude_config
# ---------------------------------------------------------------------------


class TestReadClaudeConfig:
    def test_nonexistent_file_returns_empty(self, temp_dir):
        result = read_claude_config(temp_dir / "missing.json")
        assert result == {}

    def test_empty_file_returns_empty(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("")
        result = read_claude_config(config_path)
        assert result == {}

    def test_invalid_json_returns_empty(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{invalid json")
        result = read_claude_config(config_path)
        assert result == {}

    def test_valid_json(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"mcpServers": {"other": {"command": "other-server"}}}
        config_path.write_text(json.dumps(data))
        result = read_claude_config(config_path)
        assert result == data

    def test_reads_existing_mcp_servers(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "mcpServers": {
                "my-tool": {"command": "my-tool", "args": ["--flag"]},
            },
            "otherKey": True,
        }
        config_path.write_text(json.dumps(data))
        result = read_claude_config(config_path)
        assert "my-tool" in result["mcpServers"]
        assert result["otherKey"] is True


# ---------------------------------------------------------------------------
# write_claude_config
# ---------------------------------------------------------------------------


class TestWriteClaudeConfig:
    def test_creates_parent_dirs(self, temp_dir):
        path = temp_dir / "a" / "b" / "config.json"
        write_claude_config(path, {"key": "value"})
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == {"key": "value"}

    def test_overwrites_existing(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('{"old": true}')
        write_claude_config(config_path, {"new": True})
        data = json.loads(config_path.read_text())
        assert data == {"new": True}
        assert "old" not in data

    def test_writes_formatted_json(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        write_claude_config(config_path, {"a": 1})
        text = config_path.read_text()
        assert "  " in text  # indented
        assert text.endswith("\n")


# ---------------------------------------------------------------------------
# install (integration)
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_creates_config(self, protocol, config_path):
        added, updated = install(protocol, "/project/proto.yml", "myapp", config_path)
        assert len(added) == 2
        assert len(updated) == 0
        assert config_path.exists()

    def test_install_creates_correct_entries(self, protocol, config_path):
        install(protocol, "/project/proto.yml", "myapp", config_path)
        data = json.loads(config_path.read_text())
        assert "mcpServers" in data
        assert "snodo-myapp-producer" in data["mcpServers"]
        assert "snodo-myapp-reviewer" in data["mcpServers"]

    def test_install_derives_project_name(self, protocol, config_path, temp_dir):
        proto = temp_dir / ".snodo" / "protocol.yml"
        proto.parent.mkdir()
        proto.touch()
        expected_name = sanitize_project_name(temp_dir.name)
        added, _ = install(protocol, str(proto), config_path=config_path)
        assert f"snodo-{expected_name}-producer" in added

    def test_install_preserves_existing_servers(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "mcpServers": {
                "my-other-tool": {"command": "other", "args": []},
            }
        }
        config_path.write_text(json.dumps(existing))

        install(protocol, "/project/proto.yml", "myapp", config_path)

        data = json.loads(config_path.read_text())
        assert "my-other-tool" in data["mcpServers"]
        assert "snodo-myapp-producer" in data["mcpServers"]
        assert "snodo-myapp-reviewer" in data["mcpServers"]

    def test_install_preserves_other_config_keys(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "mcpServers": {},
            "theme": "dark",
            "globalShortcut": "Ctrl+Space",
        }
        config_path.write_text(json.dumps(existing))

        install(protocol, "/p.yml", "myapp", config_path)

        data = json.loads(config_path.read_text())
        assert data["theme"] == "dark"
        assert data["globalShortcut"] == "Ctrl+Space"

    def test_install_updates_existing_snodo_entries(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        old = {
            "mcpServers": {
                "snodo-myapp-producer": {
                    "command": "snodo",
                    "args": ["serve", "--protocol", "/old/path.yml", "--mode", "producer"],
                },
            }
        }
        config_path.write_text(json.dumps(old))

        added, updated = install(protocol, "/new/path.yml", "myapp", config_path)
        assert "snodo-myapp-producer" in updated
        assert "snodo-myapp-reviewer" in added

        data = json.loads(config_path.read_text())
        producer_args = data["mcpServers"]["snodo-myapp-producer"]["args"]
        assert "/new/path.yml" in producer_args

    def test_install_returns_added_and_updated(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "mcpServers": {
                "snodo-myapp-producer": {"command": "snodo", "args": []},
            }
        }
        config_path.write_text(json.dumps(existing))

        added, updated = install(protocol, "/p.yml", "myapp", config_path)
        assert added == ["snodo-myapp-reviewer"]
        assert updated == ["snodo-myapp-producer"]

    def test_install_empty_config_file(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("")

        added, updated = install(protocol, "/p.yml", "myapp", config_path)
        assert len(added) == 2

    def test_install_no_existing_file(self, protocol, config_path):
        assert not config_path.exists()
        added, updated = install(protocol, "/p.yml", "myapp", config_path)
        assert len(added) == 2
        assert config_path.exists()

    def test_install_single_mode_protocol(self, single_mode_protocol, config_path):
        added, updated = install(single_mode_protocol, "/p.yml", "myapp", config_path)
        assert len(added) == 1
        assert added == ["snodo-myapp-producer"]

    def test_double_install_idempotent(self, protocol, config_path):
        install(protocol, "/p.yml", "myapp", config_path)
        added, updated = install(protocol, "/p.yml", "myapp", config_path)
        assert len(added) == 0
        assert len(updated) == 2

        data = json.loads(config_path.read_text())
        assert len(data["mcpServers"]) == 2

    def test_multiple_projects_coexist(self, protocol, config_path):
        install(protocol, "/project_a/.snodo/proto.yml", "app_a", config_path)
        install(protocol, "/project_b/.snodo/proto.yml", "app_b", config_path)

        data = json.loads(config_path.read_text())
        assert "snodo-app_a-producer" in data["mcpServers"]
        assert "snodo-app_a-reviewer" in data["mcpServers"]
        assert "snodo-app_b-producer" in data["mcpServers"]
        assert "snodo-app_b-reviewer" in data["mcpServers"]
        assert len(data["mcpServers"]) == 4


# ---------------------------------------------------------------------------
# print_install_result
# ---------------------------------------------------------------------------


class TestPrintInstallResult:
    def test_prints_added(self, capsys, temp_dir):
        print_install_result(["snodo-myapp-producer"], [], temp_dir / "config.json")
        captured = capsys.readouterr()
        assert "1 MCP server(s)" in captured.out
        assert "+ snodo-myapp-producer" in captured.out
        assert "Restart Claude Desktop" in captured.out

    def test_prints_updated(self, capsys, temp_dir):
        print_install_result([], ["snodo-myapp-producer"], temp_dir / "config.json")
        captured = capsys.readouterr()
        assert "1 MCP server(s)" in captured.out
        assert "~ snodo-myapp-producer" in captured.out

    def test_prints_mixed(self, capsys, temp_dir):
        print_install_result(
            ["snodo-myapp-reviewer"],
            ["snodo-myapp-producer"],
            temp_dir / "config.json",
        )
        captured = capsys.readouterr()
        assert "2 MCP server(s)" in captured.out
        assert "+ snodo-myapp-reviewer" in captured.out
        assert "~ snodo-myapp-producer" in captured.out

    def test_prints_config_path(self, capsys, temp_dir):
        path = temp_dir / "Claude" / "config.json"
        print_install_result(["a"], [], path)
        captured = capsys.readouterr()
        assert str(path) in captured.out

    def test_restart_message(self, capsys, temp_dir):
        print_install_result(["a"], [], temp_dir / "c.json")
        captured = capsys.readouterr()
        assert "Restart Claude Desktop" in captured.out


# ---------------------------------------------------------------------------
# CLI integration (snodo serve --install)
# ---------------------------------------------------------------------------


class TestCLIInstall:
    @pytest.fixture
    def initialized_project(self, temp_dir):
        """Create a temp dir with .snodo/protocol.yml."""
        snodo_dir = temp_dir / ".snodo"
        snodo_dir.mkdir()
        proto = temp_dir / ".snodo" / "protocol.yml"
        proto.write_text(yaml.dump(MINIMAL_PROTOCOL_DATA))
        return temp_dir

    def _project_name(self, project_dir):
        return sanitize_project_name(project_dir.name)

    def test_serve_install_flag(self, initialized_project, config_path):
        from snodo.cli.main import main

        pname = self._project_name(initialized_project)
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--install"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert f"snodo-{pname}-producer" in data["mcpServers"]
        assert f"snodo-{pname}-reviewer" in data["mcpServers"]

    def test_serve_install_with_project_name(self, initialized_project, config_path):
        from snodo.cli.main import main

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--install", "--project-name", "custom_name"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        assert "snodo-custom_name-producer" in data["mcpServers"]
        assert "snodo-custom_name-reviewer" in data["mcpServers"]

    def test_serve_install_preserves_existing(self, initialized_project, config_path):
        from snodo.cli.main import main

        pname = self._project_name(initialized_project)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {"mcpServers": {"other-server": {"command": "other"}}}
        config_path.write_text(json.dumps(existing))

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--install"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        assert "other-server" in data["mcpServers"]
        assert f"snodo-{pname}-producer" in data["mcpServers"]

    def test_serve_install_missing_protocol(self, temp_dir, config_path):
        from snodo.cli.main import main

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            result = main(["serve", "--install"])
        finally:
            os.chdir(original_cwd)

        assert result == 1

    def test_serve_install_success_message(self, initialized_project, config_path, capsys):
        from snodo.cli.main import main

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                main(["serve", "--install"])
        finally:
            os.chdir(original_cwd)

        captured = capsys.readouterr()
        assert "Installed" in captured.out
        assert "Restart Claude Desktop" in captured.out

    def test_serve_install_with_custom_protocol(self, temp_dir, config_path):
        from snodo.cli.main import main

        proto = temp_dir / "custom.yml"
        proto.write_text(yaml.dump(MINIMAL_PROTOCOL_DATA))

        pname = self._project_name(temp_dir)
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--install", "--protocol", str(proto)])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        producer = data["mcpServers"][f"snodo-{pname}-producer"]
        assert str(proto.resolve()) in producer["args"][2]

    def test_serve_install_unsupported_os(self, initialized_project):
        from snodo.cli.main import main

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", side_effect=RuntimeError("Unsupported platform: FreeBSD")):
                result = main(["serve", "--install"])
        finally:
            os.chdir(original_cwd)

        assert result == 1


# ---------------------------------------------------------------------------
# uninstall (unit)
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_project_entries(self, protocol, config_path):
        install(protocol, "/project/proto.yml", "myapp", config_path)
        removed = uninstall(protocol, "/project/proto.yml", project_name="myapp", config_path=config_path)
        assert sorted(removed) == ["snodo-myapp-producer", "snodo-myapp-reviewer"]
        data = json.loads(config_path.read_text())
        assert len(data["mcpServers"]) == 0

    def test_uninstall_preserves_other_servers(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "mcpServers": {
                "other-tool": {"command": "other"},
                "snodo-myapp-producer": {"command": "snodo", "args": []},
                "snodo-myapp-reviewer": {"command": "snodo", "args": []},
            }
        }
        config_path.write_text(json.dumps(existing))

        removed = uninstall(protocol, "/project/proto.yml", project_name="myapp", config_path=config_path)
        data = json.loads(config_path.read_text())
        assert "other-tool" in data["mcpServers"]
        assert len(removed) == 2

    def test_uninstall_preserves_other_projects(self, protocol, config_path):
        install(protocol, "/a/proto.yml", "app_a", config_path)
        install(protocol, "/b/proto.yml", "app_b", config_path)

        removed = uninstall(protocol, "/a/proto.yml", project_name="app_a", config_path=config_path)
        assert len(removed) == 2

        data = json.loads(config_path.read_text())
        assert "snodo-app_b-producer" in data["mcpServers"]
        assert "snodo-app_b-reviewer" in data["mcpServers"]
        assert "snodo-app_a-producer" not in data["mcpServers"]

    def test_uninstall_single_mode(self, protocol, config_path):
        install(protocol, "/project/proto.yml", "myapp", config_path)
        removed = uninstall(protocol, "/project/proto.yml", project_name="myapp", mode_id="producer", config_path=config_path)
        assert removed == ["snodo-myapp-producer"]
        data = json.loads(config_path.read_text())
        assert "snodo-myapp-reviewer" in data["mcpServers"]
        assert "snodo-myapp-producer" not in data["mcpServers"]

    def test_uninstall_nonexistent_mode(self, protocol, config_path):
        install(protocol, "/project/proto.yml", "myapp", config_path)
        removed = uninstall(protocol, "/project/proto.yml", project_name="myapp", mode_id="nonexistent", config_path=config_path)
        assert removed == []

    def test_uninstall_empty_config(self, protocol, config_path):
        removed = uninstall(protocol, "/project/proto.yml", project_name="myapp", config_path=config_path)
        assert removed == []

    def test_uninstall_no_matching_entries(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        removed = uninstall(protocol, "/project/proto.yml", project_name="myapp", config_path=config_path)
        assert removed == []

    def test_uninstall_does_not_write_if_nothing_removed(self, protocol, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        original = {"mcpServers": {"other": {"command": "x"}}}
        config_path.write_text(json.dumps(original))
        mtime_before = config_path.stat().st_mtime

        import time
        time.sleep(0.01)
        uninstall(protocol, "/project/proto.yml", project_name="myapp", config_path=config_path)
        mtime_after = config_path.stat().st_mtime
        assert mtime_before == mtime_after


# ---------------------------------------------------------------------------
# uninstall_all (unit)
# ---------------------------------------------------------------------------


class TestUninstallAll:
    def test_removes_all_snodo_entries(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "mcpServers": {
                "snodo-app_a-producer": {"command": "snodo", "args": []},
                "snodo-app_a-reviewer": {"command": "snodo", "args": []},
                "snodo-app_b-producer": {"command": "snodo", "args": []},
                "other-tool": {"command": "other"},
            }
        }
        config_path.write_text(json.dumps(config))

        removed = uninstall_all(config_path)
        assert sorted(removed) == ["snodo-app_a-producer", "snodo-app_a-reviewer", "snodo-app_b-producer"]
        data = json.loads(config_path.read_text())
        assert data["mcpServers"] == {"other-tool": {"command": "other"}}

    def test_preserves_non_snodo_entries(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "mcpServers": {
                "snodo-myapp-producer": {"command": "snodo"},
                "my-custom-tool": {"command": "custom"},
                "another-tool": {"command": "other"},
            }
        }
        config_path.write_text(json.dumps(config))

        uninstall_all(config_path)
        data = json.loads(config_path.read_text())
        assert "my-custom-tool" in data["mcpServers"]
        assert "another-tool" in data["mcpServers"]

    def test_empty_config(self, config_path):
        removed = uninstall_all(config_path)
        assert removed == []

    def test_no_snodo_entries(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        removed = uninstall_all(config_path)
        assert removed == []

    def test_does_not_write_if_nothing_removed(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        original = {"mcpServers": {"other": {"command": "x"}}}
        config_path.write_text(json.dumps(original))
        mtime_before = config_path.stat().st_mtime

        import time
        time.sleep(0.01)
        uninstall_all(config_path)
        mtime_after = config_path.stat().st_mtime
        assert mtime_before == mtime_after


# ---------------------------------------------------------------------------
# print_uninstall_result
# ---------------------------------------------------------------------------


class TestPrintUninstallResult:
    def test_prints_removed(self, capsys, temp_dir):
        print_uninstall_result(["snodo-myapp-producer", "snodo-myapp-reviewer"], temp_dir / "c.json")
        captured = capsys.readouterr()
        assert "Removed 2 MCP server(s)" in captured.out
        assert "- snodo-myapp-producer" in captured.out
        assert "- snodo-myapp-reviewer" in captured.out
        assert "Restart Claude Desktop" in captured.out

    def test_prints_nothing_found(self, capsys, temp_dir):
        print_uninstall_result([], temp_dir / "c.json")
        captured = capsys.readouterr()
        assert "No matching MCP servers found" in captured.out

    def test_prints_config_path(self, capsys, temp_dir):
        path = temp_dir / "Claude" / "config.json"
        print_uninstall_result(["snodo-myapp-x"], path)
        captured = capsys.readouterr()
        assert str(path) in captured.out


# ---------------------------------------------------------------------------
# CLI integration (snodo serve --uninstall / --uninstall-all)
# ---------------------------------------------------------------------------


class TestCLIUninstall:
    @pytest.fixture
    def initialized_project(self, temp_dir):
        """Create a temp dir with .snodo/protocol.yml."""
        snodo_dir = temp_dir / ".snodo"
        snodo_dir.mkdir()
        proto = temp_dir / ".snodo" / "protocol.yml"
        proto.write_text(yaml.dump(MINIMAL_PROTOCOL_DATA))
        return temp_dir

    def _project_name(self, project_dir):
        return sanitize_project_name(project_dir.name)

    def test_uninstall_removes_entries(self, initialized_project, config_path):
        from snodo.cli.main import main

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                main(["serve", "--install"])
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--uninstall"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        assert len(data["mcpServers"]) == 0

    def test_uninstall_with_mode_flag(self, initialized_project, config_path):
        from snodo.cli.main import main

        pname = self._project_name(initialized_project)
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                main(["serve", "--install"])
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--uninstall", "--mode", "producer"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        assert f"snodo-{pname}-producer" not in data["mcpServers"]
        assert f"snodo-{pname}-reviewer" in data["mcpServers"]

    def test_uninstall_with_project_name(self, initialized_project, config_path):
        from snodo.cli.main import main

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                main(["serve", "--install", "--project-name", "custom"])
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--uninstall", "--project-name", "custom"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        assert len(data["mcpServers"]) == 0

    def test_uninstall_preserves_other_servers(self, initialized_project, config_path):
        from snodo.cli.main import main

        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {"mcpServers": {"other-server": {"command": "other"}}}
        config_path.write_text(json.dumps(existing))

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                main(["serve", "--install"])
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--uninstall"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        assert "other-server" in data["mcpServers"]

    def test_uninstall_all_removes_everything(self, initialized_project, config_path):
        from snodo.cli.main import main

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "mcpServers": {
                "snodo-app_a-producer": {"command": "snodo"},
                "snodo-app_b-reviewer": {"command": "snodo"},
                "non-snodo-tool": {"command": "other"},
            }
        }
        config_path.write_text(json.dumps(config))

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--uninstall-all"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        data = json.loads(config_path.read_text())
        assert "non-snodo-tool" in data["mcpServers"]
        assert len(data["mcpServers"]) == 1

    def test_uninstall_success_message(self, initialized_project, config_path, capsys):
        from snodo.cli.main import main

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                main(["serve", "--install"])
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                main(["serve", "--uninstall"])
        finally:
            os.chdir(original_cwd)

        captured = capsys.readouterr()
        assert "Removed" in captured.out
        assert "Restart Claude Desktop" in captured.out

    def test_uninstall_all_nothing_to_remove(self, initialized_project, config_path, capsys):
        from snodo.cli.main import main

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"mcpServers": {}}))

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(initialized_project)
            with patch("snodo.mcp.installer.get_claude_config_path", return_value=config_path):
                result = main(["serve", "--uninstall-all"])
        finally:
            os.chdir(original_cwd)

        assert result == 0
        captured = capsys.readouterr()
        assert "No matching MCP servers found" in captured.out
