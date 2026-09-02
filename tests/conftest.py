"""Shared test fixtures and constants.

FILE: tests/conftest.py

TEST_SECRET: 32+ byte HMAC key to avoid JWT InsecureKeyLengthWarning
(RFC 7518 Section 3.2 recommends ≥32 bytes for SHA256).
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path

import pytest

from tests.verification_gate import build_notice

TEST_SECRET = "test-secret-key-that-is-at-least-32-bytes!!"


def _suite_repo_root() -> Path | None:
    """Return the git root of the repository the test suite runs in, or None.

    Tests must operate on isolated fixture repositories. This locates the repo
    that contains the suite so the HEAD guard can watch it.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    root = out.stdout.strip()
    return Path(root) if root else None


def _head_state(repo_root: Path) -> tuple:
    """Return (branch, commit) of *repo_root*'s HEAD."""
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return branch, commit


def _branch_set(repo_root: Path) -> set:
    """Return the set of branch names in *repo_root*."""
    out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    return {line for line in out.splitlines() if line}


@pytest.fixture(scope="session", autouse=True)
def _guard_suite_repo_unchanged():
    """Fail the suite if any test mutates the repository it runs in.

    Tests must operate on isolated fixture repositories. A test that lets the
    engine's executor create a task branch in the suite's own working tree
    (e.g. by building a graph without a fixture ``project_root``) creates a
    branch and moves HEAD, silently redirecting every subsequent commit. Record
    the suite repo's HEAD and branch set at session start and assert both are
    unchanged at session end.

    The same invariant covers the suite repo's own ``.snodo/`` directory
    (Fixes #65): code under test is not always the project under test, and a
    cwd-relative audit log (``.snodo/audit.log``) can point here. Under
    ``-n auto`` concurrent workers then append to this file simultaneously,
    corrupt the hash chain, and break the next run. Any content written under
    the suite repo's ``.snodo/`` during the session is a test-time write
    escaping into the repository running the tests.
    """
    repo_root = _suite_repo_root()
    if repo_root is None:
        yield
        return
    before_head = _head_state(repo_root)
    before_branches = _branch_set(repo_root)
    before_snodo = _snodo_dir_state(repo_root)
    yield
    after_head = _head_state(repo_root)
    after_branches = _branch_set(repo_root)
    after_snodo = _snodo_dir_state(repo_root)
    assert after_head == before_head, (
        "A test mutated the repository the test suite runs in: HEAD moved from "
        f"{before_head} to {after_head}. Tests must operate on isolated fixture "
        "repositories and never create branches in or change the checkout of "
        "the suite repository."
    )
    assert after_branches == before_branches, (
        "A test created or deleted a branch in the repository the test suite "
        f"runs in: branches changed from {sorted(before_branches)} to "
        f"{sorted(after_branches)}. Tests must operate on isolated fixture "
        "repositories."
    )
    assert after_snodo == before_snodo, (
        "A test wrote under the suite repository's own .snodo/ directory: the "
        f"directory state changed from {before_snodo} to {after_snodo}. "
        "Code under test must never resolve an audit log (or any other state) "
        "relative to the repository running the tests — use an isolated "
        "fixture repository instead (Fixes #65)."
    )


def _snodo_dir_state(repo_root) -> dict:
    """Return a fingerprint of the suite repo's .snodo/ directory, if any.

    Maps each relative path to a content hash, so both new files and content
    changes to existing files (e.g. an audit log that gained lines) are
    detected. Absent directory -> None (no suite .snodo/ to protect).
    """
    snodo_dir = repo_root / ".snodo"
    if not snodo_dir.is_dir():
        return None
    state: dict = {}
    for p in sorted(snodo_dir.rglob("*")):
        if p.is_file():
            try:
                data = p.read_bytes()
            except OSError:
                data = b"<unreadable>"
            state[str(p.relative_to(snodo_dir))] = hashlib.sha256(data).hexdigest()
    return state


@pytest.fixture(scope="session", autouse=True)
def _isolate_tempdir(tmp_path_factory):
    """Redirect all temp allocation under a private per-session directory.

    On macOS ``$TMPDIR`` (``/var/folders/.../T``) is shared across every
    process and persists indefinitely. Tests that run ``init`` or resolve a
    project root via ``tempfile.mkdtemp()`` could otherwise write a ``.snodo``
    at — or walk up into — that shared root, tripping the nested-init guard
    for every other test and run on the machine. Pinning ``tempfile.tempdir``
    (and ``$TMPDIR`` for subprocesses) to an isolated session dir makes that
    impossible. Linux CI is unaffected (fresh ``/tmp`` per job).
    """
    root = str(tmp_path_factory.mktemp("snodo_session"))
    old_tempdir = tempfile.tempdir
    old_env = os.environ.get("TMPDIR")
    tempfile.tempdir = root
    os.environ["TMPDIR"] = root
    yield
    tempfile.tempdir = old_tempdir
    if old_env is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = old_env


@pytest.fixture(autouse=True)
def isolate_snodo_home(monkeypatch):
    """Ensure no test reads/writes the real ~/.snodo/.

    Sets SNODO_HOME to a unique temp directory per test session
    so that resolve_home() never falls back to the real home dir.
    Also sets GIT_TERMINAL_PROMPT=0 so no test hangs on a git
    credential prompt (offline safety net), and SNODO_TOKEN_SECRET to a
    fixed shared secret so token issuance/verification is deterministic
    and the consumed-token store lives under the isolated SNODO_HOME.
    Resets the process-global audit log singleton before and after each test
    so audit events never leak across tests or touch the suite repository.
    The fixture cleans up after itself.
    """
    from snodo.infrastructure.audit import reset_global_audit_log

    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    home = tempfile.mkdtemp(prefix="snodo_test_")
    monkeypatch.setenv("SNODO_HOME", home)
    monkeypatch.setenv("SNODO_TOKEN_SECRET", TEST_SECRET)
    # The audit log is a property of the PROJECT, not the user (Fixes #111), so
    # SNODO_HOME must not redirect it. In-process tests that call get_audit_log()
    # without an explicit path would otherwise resolve to the cwd's
    # .snodo/audit.log — which, when cwd is the suite repository, is the exact
    # write-into-the-suite-repo failure #96 fixed. SNODO_AUDIT_LOG is an
    # explicit test-only override that keeps those calls off the suite repo.
    monkeypatch.setenv("SNODO_AUDIT_LOG", str(Path(home) / "audit.log"))

    reset_global_audit_log()
    yield
    reset_global_audit_log()
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def test_secret() -> str:
    """Return a 32+ byte secret for JWT signing in tests."""
    return TEST_SECRET


def pytest_configure(config: pytest.Config) -> None:
    """Validate pytest rootdir at configuration time.

    Fails loud if pytest resolves a rootdir that is not the workspace root
    (e.g., when executed from inside a package subdirectory or sub-folder).
    """
    repo_root = _suite_repo_root()
    if repo_root is None:
        return

    rootpath = getattr(config, "rootpath", None)
    resolved_root = Path(rootpath if rootpath is not None else str(config.rootdir)).resolve()
    expected_root = repo_root.resolve()
    if resolved_root != expected_root:
        raise pytest.UsageError(
            f"pytest rootdir mismatch: resolved '{resolved_root}', but workspace root is '{expected_root}'. "
            "Running pytest with a package-local or subdirectory rootdir causes silent under-collection. "
            "Run pytest from the repository root."
        )


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    """Enforce minimum collected test count when targeting the full test suite.

    Prevents silent under-collection (e.g., collecting ~450 tests instead of ~2500+).
    Only applies when running full suite paths (e.g., 'tests/' or default) without `-k` or `-m` filters.
    """
    MIN_EXPECTED_TESTS = 2000

    if config.getoption("keyword", None) or config.getoption("markexpr", None):
        return

    args = getattr(config, "args", [])
    is_full_suite = not args or all(str(arg).rstrip("/") in {"", ".", "tests"} for arg in args)

    if is_full_suite and len(items) < MIN_EXPECTED_TESTS:
        raise pytest.UsageError(
            f"Under-collection detected: collected only {len(items)} tests, but full suite expects >= {MIN_EXPECTED_TESTS}. "
            f"Rootdir: {config.rootdir}. Ensure you are targeting the full suite from the repository root."
        )


@pytest.fixture(autouse=True)
def _reset_global_mock_mode():
    """Mock mode is a process-global latch set by production code
    (engine/loop.py) and never cleared. Without this, any test that
    builds a graph with use_mock_coder=True poisons every test that
    follows it in the same process — invisible under -n auto, fatal
    under a serial run.
    """
    from snodo.coders.mock import set_mock_mode
    set_mock_mode(False)
    yield
    set_mock_mode(False)


def _get_system_tmp_roots() -> set[Path]:
    roots = {
        Path("/tmp").resolve(),
        Path("/var/tmp").resolve(),
        Path("/private/tmp").resolve(),
        Path("/private/var/tmp").resolve(),
    }
    tmpdir_env = os.environ.get("TMPDIR")
    if tmpdir_env:
        try:
            roots.add(Path(tmpdir_env).resolve())
        except Exception:
            pass
    try:
        roots.add(Path(tempfile.gettempdir()).resolve())
    except Exception:
        pass
    for p in list(roots):
        p_str = str(p).replace("\\", "/")
        if p.name == "T" and ("var/folders" in p_str or "private/var/folders" in p_str):
            roots.add(p)
    return roots


def pytest_sessionstart(session):
    _E2E_DESELECTED[0] = 0
    for root in _get_system_tmp_roots():
        target = root / ".snodo"
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _guard_no_temp_snodo_leak(request):
    old_snodo_env = {
        k: os.environ[k]
        for k in ["SNODO_PROJECT_ROOT", "SNODO_JOB_ID", "SNODO_WORKTREE_PATH", "SNODO_HOME"]
        if k in os.environ
    }
    yield
    for k in ["SNODO_PROJECT_ROOT", "SNODO_JOB_ID", "SNODO_WORKTREE_PATH", "SNODO_HOME"]:
        if k in old_snodo_env:
            os.environ[k] = old_snodo_env[k]
        else:
            os.environ.pop(k, None)
    for root in _get_system_tmp_roots():
        target = root / ".snodo"
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            pytest.fail(f"TEST LEAKED .snodo TO {root}: {request.node.nodeid}", pytrace=False)


@pytest.fixture(autouse=True)
def _guard_no_env_leak(request):
    """Fail a test that mutates os.environ without undoing it.

    ``provider_env`` and friends write to ``os.environ`` directly rather than
    through ``monkeypatch``, so nothing restores them and the change survives
    into every later test in the same process — a third instance of today's
    global-state leaks (repo mutation, stray ``.snodo``, and now env vars). The
    env vars simply were not covered by the existing guards.

    This mirrors them. It is a no-dependency autouse fixture, so pytest sets it
    up before the ``monkeypatch``-backed isolation fixtures and tears it down
    after they have undone their own ``setenv``/``delenv`` changes. The
    environment at teardown is therefore the baseline plus only what no fixture
    cleaned up — i.e. genuine leaks. Those are reverted (so one leak cannot
    poison the rest of the run) and reported against the offending test.

    A handful of names are exempt: pytest's own per-phase bookkeeping, and
    process-wide defaults that third-party libraries set lazily on first import
    (tiktoken's ``TIKTOKEN_CACHE_DIR``). These are left set rather than
    scrubbed, so they become part of the ambient baseline and are not flagged on
    later tests — unlike a per-test leak, which is reverted and fails.
    """
    baseline = dict(os.environ)
    yield
    exempt = {"PYTEST_CURRENT_TEST", "TIKTOKEN_CACHE_DIR"}
    added = sorted(set(os.environ) - set(baseline) - exempt)
    removed = sorted(set(baseline) - set(os.environ) - exempt)
    changed = sorted(
        k for k in (set(os.environ) & set(baseline)) - exempt if os.environ[k] != baseline[k]
    )
    if added or removed or changed:
        # Revert only the leaked keys, so exempt/ambient vars are untouched.
        for key in added:
            os.environ.pop(key, None)
        for key in removed:
            os.environ[key] = baseline[key]
        for key in changed:
            os.environ[key] = baseline[key]
        pytest.fail(
            "TEST LEAKED environment variables: mutated os.environ without "
            f"undoing it — added={added} removed={removed} changed={changed} in "
            f"{request.node.nodeid}. Write env through monkeypatch.setenv/delenv "
            "(restored automatically), or snapshot and restore os.environ "
            "explicitly (Fixes #200).",
            pytrace=False,
        )


# --- Reduced-gate announcement (Refs #87) -----------------------------------
#
# The documented local command runs with pyproject's addopts, which deselect the
# e2e marker, while CI clears the filter and adds coverage. That divergence was
# silent: a contributor could pass the reduced local run and be surprised by a
# CI failure it cannot see. These hooks make the reduction announce itself on the
# same screen — see tests/verification_gate.py for the source of truth.


# Count of e2e-marked items deselected in this process. Reset at collection
# start so a reused process reports a per-session number, not a running total.
_E2E_DESELECTED: list[int] = [0]


def _e2e_excluded_by_markexpr(config: pytest.Config) -> bool:
    """True when the effective marker filter excludes the e2e suite.

    Evaluated against a hypothetical item carrying only the e2e marker, so it is
    reason-accurate: a ``-k`` run that happens to drop e2e tests does not read as
    "e2e deselected by the marker filter", and ``-m e2e`` (which selects e2e) does
    not either. Falls back to a literal match on the documented reduction if the
    pinned pytest internals are unavailable.
    """
    markexpr = (config.getoption("markexpr", default="") or "").strip()
    if not markexpr:
        return False
    try:
        from _pytest.mark import Mark, MarkMatcher, _parse_expression

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            expr = _parse_expression(markexpr, "invalid marker expression")
            e2e_only = Mark(name="e2e", args=(), kwargs={})
            return not expr.evaluate(MarkMatcher.from_markers([e2e_only]))
    except Exception:
        return markexpr == "not e2e"


def pytest_deselected(items: list) -> None:
    for item in items:
        if item.get_closest_marker("e2e") is not None:
            _E2E_DESELECTED[0] += 1


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Announce the reduced local gate so a passing local run cannot be mistaken
    for the CI gate (Refs #87).

    Fires only when the effective marker filter excluded e2e — so it prints for
    the documented ``pytest tests/`` local command, and stays silent for CI's
    ``-m ""`` full run and for ``-m e2e``.
    """
    if not _e2e_excluded_by_markexpr(config):
        return
    count = _E2E_DESELECTED[0] or None
    if count is None:
        # Under xdist the deselection happened in workers, so this controller
        # has no marker-level count. For the documented reduction every
        # deselected item is an e2e test, so the aggregated count is exact.
        deselected = terminalreporter.stats.get("deselected") or []
        markexpr = (config.getoption("markexpr", default="") or "").strip()
        if markexpr == "not e2e" and deselected:
            count = len(deselected)
    for line in build_notice(count).splitlines():
        terminalreporter.write_line(line)

