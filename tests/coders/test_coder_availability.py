"""Adapters declare their environment needs; the shared check reads them.

FILE: tests/coders/test_coder_availability.py

``check_coder_available`` is the one ``shutil.which`` a dispatcher pays before
submitting a job. It must ask each adapter what IT needs (a host CLI binary,
a container runtime, or nothing at all) in the process that will invoke it —
not the shell where ``snodo ready`` happened to run.
"""

from unittest import mock

from snodo.coders import check_coder_available

_PATCH = "snodo.coders.availability.shutil.which"


def test_subprocess_coders_report_their_binary_and_install_hint():
    with mock.patch(_PATCH, return_value=None):
        problem = check_coder_available("opencode-cli")
    assert problem is not None
    binary, remediation = problem
    assert binary == "opencode"
    assert "Install opencode" in remediation

    with mock.patch(_PATCH, return_value=None):
        problem = check_coder_available("agy")
    assert problem is not None
    assert problem[0] == "agy"
    assert "antigravity.google" in problem[1]


def test_container_coder_reports_the_docker_runtime():
    with mock.patch(_PATCH, return_value=None):
        problem = check_coder_available("opencode")
    assert problem is not None
    assert problem[0] == "docker"
    assert "Docker" in problem[1]


def test_present_binaries_pass_the_check():
    def _which(name):
        return f"/usr/local/bin/{name}"

    with mock.patch(_PATCH, side_effect=_which):
        assert check_coder_available("opencode-cli") is None
        assert check_coder_available("agy") is None
        assert check_coder_available("opencode") is None


def test_api_and_mock_coders_declare_nothing_to_install():
    """Coders that invoke no program must never be refused over PATH."""
    with mock.patch(_PATCH, return_value=None):
        assert check_coder_available("litellm") is None
        assert check_coder_available("mock") is None
        assert check_coder_available("anthropic") is None


def test_unknown_coder_name_is_not_invented_as_a_path_fault():
    with mock.patch(_PATCH, return_value=None):
        assert check_coder_available("does-not-exist") is None
