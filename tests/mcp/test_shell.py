"""Tests for Shell/Test Runner MCP server.

FILE: tests/mcp/test_shell.py

Tests cover:
- Test execution with pytest, npm, cargo
- Output parsing and ValidatorResult conversion
- Exit code handling
- Command whitelist security
- Error conditions
- 100% coverage
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

from snodo.mcp.shell import (
    ShellMCP, CommandNotAllowedError, get_shell
)
from snodo.core.interfaces import ValidatorResult


@pytest.fixture
def temp_project():
    """Create a temporary project directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        shell_mcp = ShellMCP(tmpdir, validator_id="test")
        yield shell_mcp, tmpdir


# ========== INITIALIZATION TESTS ==========

def test_shell_init_with_valid_root(temp_project):
    """Test initializing ShellMCP with valid root."""
    shell_mcp, tmpdir = temp_project
    assert shell_mcp.project_root == Path(tmpdir).resolve()
    assert shell_mcp.validator_id == "test"


def test_shell_init_nonexistent_root_raises():
    """Test initializing with nonexistent root raises."""
    with pytest.raises(ValueError, match="does not exist"):
        ShellMCP("/nonexistent/path/xyz123")


def test_shell_init_file_as_root_raises():
    """Test initializing with file as root raises."""
    with tempfile.NamedTemporaryFile() as tmpfile:
        with pytest.raises(ValueError, match="not a directory"):
            ShellMCP(tmpfile.name)


# ========== COMMAND VALIDATION TESTS ==========

def test_validate_command_pytest(temp_project):
    """Test validating pytest command."""
    import sys
    shell_mcp, _ = temp_project
    command = shell_mcp._validate_command("pytest")
    assert command == [sys.executable, "-m", "pytest"]


def test_validate_command_npm(temp_project):
    """Test validating npm test command."""
    shell_mcp, _ = temp_project
    command = shell_mcp._validate_command("npm")
    assert command == ["npm", "test"]


def test_validate_command_cargo(temp_project):
    """Test validating cargo test command."""
    shell_mcp, _ = temp_project
    command = shell_mcp._validate_command("cargo")
    assert command == ["cargo", "test"]


def test_validate_command_not_allowed(temp_project):
    """Test that non-whitelisted command raises."""
    shell_mcp, _ = temp_project
    
    with pytest.raises(CommandNotAllowedError, match="not allowed"):
        shell_mcp._validate_command("rm")


def test_validate_command_arbitrary_shell(temp_project):
    """Test that arbitrary shell commands are blocked."""
    shell_mcp, _ = temp_project
    
    with pytest.raises(CommandNotAllowedError):
        shell_mcp._validate_command("bash")


# ========== RUN TESTS - SUCCESS CASES ==========

@patch('subprocess.run')
def test_run_tests_all_pass(mock_run, temp_project):
    """Test running tests when all pass."""
    shell_mcp, _ = temp_project
    
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="===== 5 passed in 0.23s =====",
        stderr=""
    )
    
    result = shell_mcp.run_tests("tests/")
    
    assert isinstance(result, ValidatorResult)
    assert result.severity == "pass"
    assert result.validator_id == "test"
    assert "passed" in result.justification


@patch('subprocess.run')
def test_run_tests_with_warnings(mock_run, temp_project):
    """Test running tests with warnings."""
    shell_mcp, _ = temp_project
    
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="===== 5 passed, 2 warnings in 0.23s =====",
        stderr=""
    )
    
    result = shell_mcp.run_tests("tests/")
    
    assert result.severity == "warn"
    assert "warning" in result.justification.lower()


@patch('subprocess.run')
def test_run_tests_with_extra_args(mock_run, temp_project):
    """Test running tests with extra arguments."""
    shell_mcp, _ = temp_project
    
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    
    shell_mcp.run_tests("tests/", extra_args=["-v", "--tb=short"])
    
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "-v" in call_args
    assert "--tb=short" in call_args


# ========== RUN TESTS - FAILURE CASES ==========

@patch('subprocess.run')
def test_run_tests_failures(mock_run, temp_project):
    """Test running tests when some fail."""
    shell_mcp, _ = temp_project
    
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="FAILED tests/test_foo.py::test_bar - AssertionError\n===== 1 failed, 4 passed =====",
        stderr=""
    )
    
    result = shell_mcp.run_tests("tests/")
    
    assert result.severity == "blocker"
    assert "failed" in result.justification.lower()


@patch('subprocess.run')
def test_run_tests_no_tests_collected(mock_run, temp_project):
    """Test running tests when none are collected."""
    shell_mcp, _ = temp_project
    
    mock_run.return_value = MagicMock(
        returncode=5,
        stdout="",
        stderr=""
    )
    
    result = shell_mcp.run_tests("tests/nonexistent/")
    
    assert result.severity == "blocker"
    assert "no tests found" in result.justification.lower()


@patch('subprocess.run')
def test_run_tests_timeout(mock_run, temp_project):
    """Test handling test timeout."""
    shell_mcp, _ = temp_project
    
    mock_run.side_effect = subprocess.TimeoutExpired("pytest", 300)
    
    result = shell_mcp.run_tests("tests/")
    
    assert result.severity == "blocker"
    assert "timed out" in result.justification.lower()


@patch('subprocess.run')
def test_run_tests_command_not_found(mock_run, temp_project):
    """Test handling when test command not found."""
    shell_mcp, _ = temp_project
    
    mock_run.side_effect = FileNotFoundError()
    
    result = shell_mcp.run_tests("tests/")
    
    assert result.severity == "blocker"
    assert "not found" in result.justification.lower()


# ========== PARSE OUTPUT TESTS ==========

def test_parse_output_success(temp_project):
    """Test parsing successful test output."""
    shell_mcp, _ = temp_project
    
    result = shell_mcp.parse_output(
        exit_code=0,
        stdout="===== 10 passed in 1.5s =====",
        stderr=""
    )
    
    assert result.severity == "pass"
    assert "passed" in result.justification


def test_parse_output_with_warnings(temp_project):
    """Test parsing output with warnings."""
    shell_mcp, _ = temp_project
    
    result = shell_mcp.parse_output(
        exit_code=0,
        stdout="DeprecationWarning: something\n===== 5 passed =====",
        stderr=""
    )
    
    assert result.severity == "warn"


def test_parse_output_failures(temp_project):
    """Test parsing output with failures."""
    shell_mcp, _ = temp_project
    
    result = shell_mcp.parse_output(
        exit_code=1,
        stdout="===== 3 failed, 7 passed =====",
        stderr=""
    )
    
    assert result.severity == "blocker"
    assert "failed" in result.justification


def test_parse_output_no_tests(temp_project):
    """Test parsing output when no tests collected."""
    shell_mcp, _ = temp_project
    
    result = shell_mcp.parse_output(
        exit_code=5,
        stdout="",
        stderr=""
    )
    
    assert result.severity == "blocker"
    assert "no tests" in result.justification.lower()


# ========== WARNING DETECTION TESTS ==========

def test_has_warnings_deprecation(temp_project):
    """Test detecting DeprecationWarning."""
    shell_mcp, _ = temp_project
    
    assert shell_mcp._has_warnings("DeprecationWarning: old api", "")


def test_has_warnings_future(temp_project):
    """Test detecting FutureWarning."""
    shell_mcp, _ = temp_project
    
    assert shell_mcp._has_warnings("", "FutureWarning: will change")


def test_has_warnings_count(temp_project):
    """Test detecting warning count."""
    shell_mcp, _ = temp_project
    
    assert shell_mcp._has_warnings("5 warnings in 0.2s", "")


def test_has_warnings_none(temp_project):
    """Test no warnings detected."""
    shell_mcp, _ = temp_project
    
    assert not shell_mcp._has_warnings("all good", "")


# ========== SUMMARY EXTRACTION TESTS ==========

def test_extract_summary_pytest(temp_project):
    """Test extracting pytest summary."""
    shell_mcp, _ = temp_project
    
    summary = shell_mcp._extract_summary(
        "===== 5 passed in 0.5s =====",
        ""
    )
    
    assert "5 passed" in summary


def test_extract_summary_npm(temp_project):
    """Test extracting npm test summary."""
    shell_mcp, _ = temp_project
    
    summary = shell_mcp._extract_summary(
        "Tests: 5 passed, 5 total",
        ""
    )
    
    assert "Tests:" in summary


def test_extract_summary_cargo(temp_project):
    """Test extracting cargo test summary."""
    shell_mcp, _ = temp_project
    
    summary = shell_mcp._extract_summary(
        "test result: ok. 5 passed; 0 failed",
        ""
    )
    
    assert "test result:" in summary


def test_extract_summary_fallback(temp_project):
    """Test summary extraction fallback."""
    shell_mcp, _ = temp_project
    
    summary = shell_mcp._extract_summary(
        "some output\nlast line here",
        ""
    )
    
    assert summary == "last line here"


# ========== FAILURE INFO EXTRACTION TESTS ==========

def test_extract_failure_info_with_test_names(temp_project):
    """Test extracting failure info with test names."""
    shell_mcp, _ = temp_project
    
    info = shell_mcp._extract_failure_info(
        "FAILED tests/test_foo.py::test_bar - Error\n2 failed",
        "",
        1
    )
    
    assert "2" in info
    assert "failed" in info.lower()


def test_extract_failure_info_from_stderr(temp_project):
    """Test extracting failure info from stderr."""
    shell_mcp, _ = temp_project
    
    info = shell_mcp._extract_failure_info(
        "",
        "Error: something went wrong",
        1
    )
    
    assert "error" in info.lower()


def test_extract_failure_info_fallback(temp_project):
    """Test failure info extraction fallback."""
    shell_mcp, _ = temp_project
    
    info = shell_mcp._extract_failure_info("", "", 1)
    
    assert "exit code 1" in info


# ========== COMMAND TYPE TESTS ==========

@patch('subprocess.run')
def test_run_tests_npm(mock_run, temp_project):
    """Test running npm tests."""
    shell_mcp, _ = temp_project
    
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    
    shell_mcp.run_tests(".", command_type="npm")
    
    call_args = mock_run.call_args[0][0]
    assert call_args[0:2] == ["npm", "test"]


@patch('subprocess.run')
def test_run_tests_cargo(mock_run, temp_project):
    """Test running cargo tests."""
    shell_mcp, _ = temp_project
    
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    
    shell_mcp.run_tests(".", command_type="cargo")
    
    call_args = mock_run.call_args[0][0]
    assert call_args[0:2] == ["cargo", "test"]


# ========== GLOBAL INSTANCE TESTS ==========

def test_get_shell_initializes(temp_project):
    """Test get_shell initializes instance."""
    _, tmpdir = temp_project
    shell = get_shell(tmpdir, "custom_id")
    
    assert isinstance(shell, ShellMCP)
    assert shell.project_root == Path(tmpdir).resolve()
    assert shell.validator_id == "custom_id"


def test_get_shell_reuses_instance(temp_project):
    """Test get_shell returns same instance."""
    _, tmpdir = temp_project
    shell1 = get_shell(tmpdir)
    shell2 = get_shell()
    
    assert shell1 is shell2


def test_get_shell_no_init_raises():
    """Test get_shell without init raises."""
    # Reset global instance
    import snodo.mcp.shell as shell_module
    shell_module._shell_instance = None
    
    with pytest.raises(ValueError, match="not initialized"):
        get_shell()


# ========== INTEGRATION TESTS ==========

@patch('subprocess.run')
def test_complete_test_workflow(mock_run, temp_project):
    """Test complete test execution workflow."""
    shell_mcp, _ = temp_project
    
    # Simulate pytest run with mixed results
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="FAILED tests/test_a.py::test_foo\nFAILED tests/test_b.py::test_bar\n===== 2 failed, 8 passed =====",
        stderr=""
    )
    
    result = shell_mcp.run_tests("tests/", extra_args=["-v"])
    
    # Verify result structure
    assert isinstance(result, ValidatorResult)
    assert result.validator_id == "test"
    assert result.severity == "blocker"
    assert "2" in result.justification
    assert "failed" in result.justification.lower()


@patch('subprocess.run')
def test_security_command_isolation(mock_run, temp_project):
    """Test that only whitelisted commands can run."""
    shell_mcp, _ = temp_project
    
    # Try to run arbitrary command
    with pytest.raises(CommandNotAllowedError):
        shell_mcp.run_tests(".", command_type="bash")
    
    # Verify subprocess was never called
    mock_run.assert_not_called()


# ========== EDGE CASES ==========

def test_empty_output(temp_project):
    """Test parsing empty output."""
    shell_mcp, _ = temp_project
    
    result = shell_mcp.parse_output(0, "", "")
    
    assert result.severity == "pass"
    assert result.justification  # Should have something


def test_very_long_output(temp_project):
    """Test handling very long output."""
    shell_mcp, _ = temp_project
    
    long_output = "x" * 10000
    result = shell_mcp.parse_output(1, long_output, "")
    
    # Should truncate
    assert len(result.justification) <= 300


def test_validator_id_custom(temp_project):
    """Test custom validator ID."""
    _, tmpdir = temp_project
    shell_mcp = ShellMCP(tmpdir, validator_id="custom_validator")
    
    result = shell_mcp.parse_output(0, "passed", "")
    
    assert result.validator_id == "custom_validator"


def test_fallback_validator_result_used_when_import_fails():
    """Trigger the ImportError fallback for ValidatorResult (lines 18-27)."""
    import subprocess
    import sys

    code = """
import sys
import types

# Fake snodo.core.interfaces without ValidatorResult to trigger ImportError
sys.modules['snodo.core'] = types.ModuleType('snodo.core')
sys.modules['snodo.core.interfaces'] = types.ModuleType('snodo.core.interfaces')

from snodo.mcp.shell import ShellMCP, ValidatorResult

# The fallback ValidatorResult should be a BaseModel that works
r = ValidatorResult(validator_id="test", severity="pass", justification="ok")
print(f"severity={r.severity}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
    assert "severity=pass" in result.stdout


def test_fallback_validator_result_in_process(monkeypatch):
    """Trigger the fallback in-process for coverage (lines 18-27)."""
    import sys
    import types
    import importlib

    old_core = sys.modules.get("snodo.core")
    old_interfaces = sys.modules.get("snodo.core.interfaces")

    fake_core = types.ModuleType("snodo.core")
    monkeypatch.setitem(sys.modules, "snodo.core", fake_core)

    fake_interfaces = types.ModuleType("snodo.core.interfaces")
    monkeypatch.setitem(sys.modules, "snodo.core.interfaces", fake_interfaces)

    import snodo.mcp.shell as shell_mod

    importlib.reload(shell_mod)

    r = shell_mod.ValidatorResult(
        validator_id="test", severity="pass", justification="ok"
    )
    assert r.severity == "pass"

    # Restore real modules so subsequent tests are unaffected
    if old_core is not None:
        sys.modules["snodo.core"] = old_core
    if old_interfaces is not None:
        sys.modules["snodo.core.interfaces"] = old_interfaces
    importlib.reload(shell_mod)
