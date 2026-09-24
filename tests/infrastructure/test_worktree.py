"""Tests for git worktree lifecycle and task-branch merge.

FILE: tests/infrastructure/test_worktree.py
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from snodo.infrastructure.worktree import (
    WorktreeIsolationError,
    check_spec_paths_exist,
    create_worktree,
    delete_task_branch,
    merge_task_branch,
    remove_worktree,
    surface_untracked_files,
    task_branch_name,
    teardown_task_worktree,
    worktree_path,
    workspace_roots,
)
from snodo.infrastructure.worktree import _spec_overlap_paths, _spec_referenced_paths
from snodo.tools.git import GitError, resolve_base_branch


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def _current_branch(root: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _branches(root: Path) -> str:
    return subprocess.run(
        ["git", "branch"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        # Keep the sibling worktree container isolated per fixture. This also
        # lets the retry tests prove preservation without cross-test paths.
        root = Path(d) / "project"
        root.mkdir()
        _init_repo(root)
        yield root


# === resolve_base_branch ===

def test_resolve_base_branch_defaults_to_main(repo):
    assert resolve_base_branch(str(repo)) == "main"


def test_remote_task_worktree_starts_from_explicit_base_commit(repo):
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (repo / "README.md").write_text("later host state\n")
    subprocess.run(["git", "commit", "-am", "later host state"], cwd=repo, check=True)

    path = create_worktree(str(repo), "remote-base-task", "use exact base", base=base_sha)

    worktree_head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert worktree_head == base_sha


def test_resolve_base_branch_uses_remote_head(repo):
    # Simulate a repository whose remote default is not main.
    subprocess.run(["git", "init", "-qb", "master"], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        cwd=repo, check=True,
    )
    assert resolve_base_branch(str(repo)) == "master"


# === unborn HEAD (no commits) — fail loud, never degrade to no isolation ===

def _init_unborn_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def test_create_worktree_on_unborn_head_raises(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _init_unborn_repo(root)

    with pytest.raises(WorktreeIsolationError, match="no commits"):
        create_worktree(str(root), "task_1", "do the thing")


def test_setup_for_task_on_unborn_head_propagates(tmp_path):
    """setup_for_task must not swallow the isolation failure and return None.

    Returning None is the silent-degradation path: the caller would run the
    agent in the operator's working tree. The structural error must surface
    so a human can decide (Fixes #29).
    """
    from snodo.infrastructure.worktree import setup_for_task

    root = tmp_path / "proj"
    root.mkdir()
    _init_unborn_repo(root)

    with pytest.raises(WorktreeIsolationError, match="no commits"):
        setup_for_task(str(root), "task_1", "do the thing")


# === create_worktree branches off the resolved base ===

def test_create_worktree_branches_off_non_main_base(repo):
    # Make the default branch "master" (rename main -> master).
    subprocess.run(["git", "branch", "-m", "main", "master"], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        cwd=repo, check=True,
    )
    # Add a commit on master so we can tell the worktree branched from it.
    (repo / "base_only.txt").write_text("base\n")
    subprocess.run(["git", "add", "base_only.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base commit"], cwd=repo, check=True)

    wt = create_worktree(str(repo), "task_1", "do the thing")

    assert wt.exists()
    assert (wt / "base_only.txt").exists()  # inherited from master, not main


def test_create_worktree_prepares_dependencies_before_return(repo, monkeypatch):
    calls = []

    def prepare(path, protocol=None):
        calls.append((Path(path), protocol))
        return type("Result", (), {"status": "executed", "command": "npm ci"})()

    monkeypatch.setattr("snodo.infrastructure.environment.prepare_environment", prepare)
    protocol = object()
    wt = create_worktree(str(repo), "task_prepare", "install dependencies", protocol=protocol)

    assert calls == [(wt, protocol)]
    assert wt.exists()


def test_create_worktree_propagates_failed_dependency_setup(repo, monkeypatch):
    from snodo.infrastructure.environment import EnvironmentPrepError

    def fail(path, protocol=None):
        raise EnvironmentPrepError("npm ci", 127, "npm: command not found")

    monkeypatch.setattr("snodo.infrastructure.environment.prepare_environment", fail)

    with pytest.raises(EnvironmentPrepError, match="npm: command not found"):
        create_worktree(str(repo), "task_prepare_fail", "install dependencies")


def test_plan_scopes_branch_and_worktree_names(repo):
    first = create_worktree(str(repo), "task_1_1", "Add the shared feature", plan_name="alpha")
    second = create_worktree(str(repo), "task_1_1", "Add the shared feature", plan_name="beta")

    assert first != second
    assert first == worktree_path(str(repo), "task_1_1", "alpha")
    assert second == worktree_path(str(repo), "task_1_1", "beta")
    branches = _branches(repo)
    assert "task/alpha/task_1_1/add-the-shared-feature" in branches
    assert "task/beta/task_1_1/add-the-shared-feature" in branches


def test_plan_run_reuses_existing_legacy_worktree(repo):
    legacy_path = create_worktree(str(repo), "task_1_1", "Add the shared feature")
    legacy_branch = task_branch_name("task_1_1", "Add the shared feature")

    reused = create_worktree(
        str(repo), "task_1_1", "Add the shared feature", plan_name="alpha"
    )

    assert reused == legacy_path
    assert legacy_path.exists()
    assert legacy_branch in _branches(repo)
    assert reused != worktree_path(str(repo), "task_1_1", "alpha")


def test_retry_reuses_existing_worktree_without_discarding_work(repo):
    """A retry keeps artifacts in an existing task worktree (ticket 4TICKET)."""
    wt = create_worktree(str(repo), "task_retry", "Implement the feature")
    (wt / "finished.py").write_text("finished = True\n")
    branch = task_branch_name("task_retry", "Implement the feature")

    reused = create_worktree(str(repo), "task_retry", "Implement the feature")

    assert reused == wt
    assert (wt / "finished.py").read_text() == "finished = True\n"
    assert branch in _branches(repo)


def test_retry_reuses_existing_branch_without_deleting_commits(repo):
    """A branch left without its checkout is reattached with its commits intact."""
    wt = create_worktree(str(repo), "task_retry", "Implement the feature")
    (wt / "finished.py").write_text("finished = True\n")
    subprocess.run(["git", "add", "finished.py"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "finished work"], cwd=wt, check=True)
    branch = task_branch_name("task_retry", "Implement the feature")
    head = subprocess.check_output(["git", "rev-parse", branch], cwd=repo, text=True).strip()
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=True)

    reused = create_worktree(str(repo), "task_retry", "Implement the feature")

    assert reused == wt
    assert (wt / "finished.py").read_text() == "finished = True\n"
    assert subprocess.check_output(
        ["git", "rev-parse", branch], cwd=repo, text=True,
    ).strip() == head


# === merge_task_branch ===

def test_merge_task_branch_success(repo):
    branch = task_branch_name("task_1", "add feature")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    (repo / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    assert merge_task_branch(str(repo), branch) == ("merged", [])

    assert _current_branch(repo) == "main"
    assert (repo / "feature.txt").exists()  # base branch now has the commit


def test_merge_task_branch_conflict(repo):
    branch = task_branch_name("task_1", "conflicting change")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    (repo / "README.md").write_text("branch content\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "branch change"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("main content\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "main change"], cwd=repo, check=True)

    status, paths = merge_task_branch(str(repo), branch)
    assert status == "conflict"
    assert paths == ["README.md"]

    # Base branch is left clean (merge aborted) and the source branch survives.
    assert _current_branch(repo) == "main"
    assert "README.md" not in _current_branch(repo)  # sanity: no conflict markers path
    assert branch in _branches(repo)


def test_merge_task_branch_nonexistent_raises(repo):
    with pytest.raises(GitError):
        merge_task_branch(str(repo), "task/nope/missing")


def test_concurrent_merges_serialize_and_both_succeed(repo):
    """Concurrent tasks merging at the same moment serialize on merge_lock and both land."""
    import concurrent.futures

    branch1 = task_branch_name("task_1", "feature one")
    subprocess.run(["git", "checkout", "-qb", branch1], cwd=repo, check=True)
    (repo / "feature1.txt").write_text("feature 1\n")
    subprocess.run(["git", "add", "feature1.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feature 1"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    branch2 = task_branch_name("task_2", "feature two")
    subprocess.run(["git", "checkout", "-qb", branch2], cwd=repo, check=True)
    (repo / "feature2.txt").write_text("feature 2\n")
    subprocess.run(["git", "add", "feature2.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feature 2"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(merge_task_branch, str(repo), branch1)
        f2 = executor.submit(merge_task_branch, str(repo), branch2)
        res1 = f1.result()
        res2 = f2.result()

    assert res1 == ("merged", [])
    assert res2 == ("merged", [])
    assert (repo / "feature1.txt").exists()
    assert (repo / "feature2.txt").exists()
    assert not (repo / ".git" / "index.lock").exists()
    assert not (repo / ".git" / "HEAD.lock").exists()



# === delete_task_branch ===

def test_delete_task_branch(repo):
    branch = task_branch_name("task_1", "delete me")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    delete_task_branch(str(repo), branch)
    assert branch not in _branches(repo)


# === teardown_task_worktree: worktree first, then only merged branches ===

def test_teardown_removes_worktree_and_merged_branch(repo):
    branch = task_branch_name("task_1", "add feature")
    create_worktree(str(repo), "task_1", "add feature")
    _commit_in_worktree(repo, "feature.txt")
    merge_task_branch(str(repo), branch)

    teardown_task_worktree(str(repo), "task_1")

    assert not worktree_path(str(repo), "task_1").exists()
    assert branch not in _branches(repo)


def test_teardown_keeps_unmerged_branch(repo):
    """A branch whose work is not in the base is the only copy — it survives."""
    branch = task_branch_name("task_1", "add feature")
    create_worktree(str(repo), "task_1", "add feature")
    _commit_in_worktree(repo, "feature.txt")

    teardown_task_worktree(str(repo), "task_1")

    assert not worktree_path(str(repo), "task_1").exists()
    assert branch in _branches(repo)


def test_remove_worktree_reconciles_metadata_after_git_failure(repo, monkeypatch):
    """A failed Git removal must not leave a ghost worktree registration."""
    from contextlib import nullcontext
    from git import GitCommandError
    from types import SimpleNamespace
    from snodo.tools.git import open_repo

    wt = create_worktree(str(repo), "task_ghost", "remove me")
    git_repo = open_repo(str(repo))

    class GitProxy:
        def worktree(self, command, *args):
            if command == "list":
                return git_repo.git.worktree(command, *args)
            raise GitCommandError(
                "git worktree remove", 1, stderr="simulated failure"
            )

    failing_repo = SimpleNamespace(common_dir=git_repo.common_dir, git=GitProxy())
    monkeypatch.setattr(
        "snodo.tools.git.open_repo", lambda project_root: nullcontext(failing_repo)
    )

    remove_worktree(str(repo), "task_ghost")

    assert not wt.exists()
    assert str(wt.resolve()) not in subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _commit_in_worktree(repo, filename):
    wt = worktree_path(str(repo), "task_1")
    (wt / filename).write_text("work\n")
    subprocess.run(["git", "add", filename], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "work"], cwd=wt, check=True)


# === spec-referenced paths must exist in the worktree (issue #93) ===

def test_check_spec_paths_exist_flags_missing_cited_file(repo):
    """A spec citing a path absent from the worktree is flagged before dispatch."""
    missing = check_spec_paths_exist(
        str(repo),
        "Implement the card footer per docs/design/card-footer-qr.html",
    )
    assert "docs/design/card-footer-qr.html" in missing


def test_check_spec_paths_exist_flags_paths_meant_to_be_created(repo):
    """A spec naming a path the task is meant to create IS flagged — that is
    the false positive the warning tolerates. The check warns rather than
    halts precisely because only the operator can tell 'to be created' from
    'cited as authority'."""
    missing = check_spec_paths_exist(
        str(repo),
        "Create src/parser.py and tests/test_parser.py",
    )
    assert "src/parser.py" in missing
    # tests/ is governance/authority-adjacent and ignored.
    assert "tests/test_parser.py" not in missing


def test_check_spec_paths_exist_ignores_governance_paths(repo):
    """Specs citing docs/decisions or .snodo are not flagged — the coder must
    not read those as authority anyway."""
    missing = check_spec_paths_exist(
        str(repo),
        "Follow docs/decisions/0001 and the .snodo protocol",
    )
    assert missing == []


def test_check_spec_paths_exist_flags_real_path_but_not_slash_prose(repo):
    """A real cited path is flagged; slash-containing prose is not (Fixes #99).

    The detector must not match anything containing a slash: on its first real
    run it flagged ``noindex/no-referrer``, which is prose in a sentence, not a
    filename. A guard that cries wolf gets ignored, and this one guards
    against a failure that already cost a whole task once.
    """
    missing = check_spec_paths_exist(
        str(repo),
        "Add rel=noindex/no-referrer to the card footer per "
        "docs/design/card-footer-qr.html",
    )
    assert "docs/design/card-footer-qr.html" in missing
    assert "noindex/no-referrer" not in missing


def test_check_spec_paths_exist_against_worktree(repo, tmp_path):
    """A file present in the project root but absent from the worktree is
    flagged when the worktree is checked — the untracked-file gap."""
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "design").mkdir(exist_ok=True)
    (repo / "docs" / "design" / "card-footer-qr.html").write_text("<html>ref</html>")
    # Untracked: exists in the operator's tree, absent from any worktree.
    # Simulate the worktree as a separate directory that does not have it.
    fake_worktree = tmp_path / "wt"
    fake_worktree.mkdir()
    missing = check_spec_paths_exist(
        str(repo),
        "Implement per docs/design/card-footer-qr.html",
        worktree=str(fake_worktree),
    )
    assert "docs/design/card-footer-qr.html" in missing


def test_check_spec_paths_exist_resolves_package_json_workspace(repo):
    package_file = repo / "app.droptrack.io" / "src" / "views" / "create" / "index.tsx"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("export default function Create() {}\n")
    (repo / "package.json").write_text('{"workspaces": ["app.*"]}\n')

    assert check_spec_paths_exist(str(repo), "Update views/create/index.tsx") == []


def test_check_spec_paths_exist_resolves_pnpm_workspace(repo):
    package_file = repo / "packages" / "api" / "src" / "routes.ts"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("export {}\n")
    (repo / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")

    assert check_spec_paths_exist(str(repo), "Update src/routes.ts") == []


def test_workspace_roots_keep_ambiguous_declared_members_ambiguous(repo):
    for package in ("packages/one", "packages/two"):
        path = repo / package / "src"
        path.mkdir(parents=True)
        (path / "shared.ts").write_text("export {}\n")
    (repo / "package.json").write_text(
        '{"workspaces": {"packages": ["packages/*"]}}\n'
    )

    assert len(workspace_roots(str(repo))) == 5
    assert check_spec_paths_exist(str(repo), "Update src/shared.ts") == ["src/shared.ts"]


def test_quoted_evidence_is_not_a_repository_citation(repo):
    spec = '''Investigate the failure.

```json
{"callback": "https://example.test/hooks/task/result"}
```

The server log said "s3/bucket/task/result.json".
'''

    assert _spec_referenced_paths(spec) == []
    assert check_spec_paths_exist(str(repo), spec) == []


def test_real_citation_outside_quoted_evidence_is_still_guarded(repo):
    missing = check_spec_paths_exist(
        str(repo),
        'Use the repository contract in `src/contracts/result.json`.\n'
        'The log contained "s3/bucket/task/result.json".',
    )

    assert missing == ["src/contracts/result.json"]


def test_framework_paths_keep_special_characters_and_drop_sentence_period():
    spec = (
        "Update app/src/routes/(dashboard)/now/+page.svelte and "
        "app/src/lib/groupSelection.ts. Also update "
        "app/[locale]/blog/[slug]/page.tsx and "
        "app/pages/@admin/users.vue. Do not confuse and/or with a path."
    )

    assert _spec_referenced_paths(spec) == [
        "app/src/routes/(dashboard)/now/+page.svelte",
        "app/src/lib/groupSelection.ts",
        "app/[locale]/blog/[slug]/page.tsx",
        "app/pages/@admin/users.vue",
    ]


def test_non_touch_path_does_not_create_overlap_citation():
    spec = "Do not touch `src/shared.py`; a sibling task owns it."

    assert _spec_referenced_paths(spec) == ["src/shared.py"]
    assert _spec_overlap_paths(spec) == []


def test_prohibited_and_reference_only_paths_do_not_create_overlap(repo):
    spec = (
        "Do not change docs/design, site/tests/visual, "
        "site/src/styles/tokens.css or any approved test: they are the owner's\n"
        "No colour, shadow or gradient literal outside "
        "site/src/styles/tokens.css\n"
        "The site's token file site/src/styles/tokens.css already equals the "
        "reference's tokens block"
    )
    expected = ["site/tests/visual", "site/src/styles/tokens.css"]

    assert _spec_referenced_paths(spec) == expected
    assert _spec_overlap_paths(spec) == []
    assert check_spec_paths_exist(str(repo), spec) == expected


def test_surface_untracked_files_lists_untracked(repo):
    (repo / "untracked.txt").write_text("new")
    (repo / "tracked.txt").write_text("tracked")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "tracked"], cwd=repo, check=True)

    untracked = surface_untracked_files(str(repo))
    assert "untracked.txt" in untracked
    assert "tracked.txt" not in untracked


def test_create_worktree_leaves_no_persistent_git_child(repo):
    """The worktree path must not leak a `git cat-file` child into later tests.

    GitPython's default object DB lazily starts a persistent
    ``git cat-file --batch-check`` subprocess on first object read and ends it
    only from ``Repo.__del__`` during a later cyclic-GC pass. `create_worktree`
    reads ``repo.head.commit``, so an unclosed Repo left that child running
    after its test returned — terminated by SIGTERM whenever GC happened to run,
    possibly under another test's process-wide ``patch("os.kill")`` (Fixes #258).
    Opening through ``open_repo`` uses the in-process object DB, so no such
    child exists. This pins the mechanism rather than trusting the guard alone.
    """
    import psutil

    def children():
        return {
            p.pid: " ".join(p.cmdline())
            for p in psutil.Process().children(recursive=True)
            if p.status() != psutil.STATUS_ZOMBIE
        }

    before = children()
    create_worktree(str(repo), "task_leakcheck", "a task that must not leak")

    leaked = {pid: cmd for pid, cmd in children().items() if pid not in before}
    assert not leaked, f"create_worktree leaked child processes: {leaked}"


# === task_branch_is_merged ===

def _init_repo_on_main(root: Path) -> None:
    subprocess.run(["git", "init", "-qb", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def _make_task_branch_with_work(root: Path) -> str:
    branch = task_branch_name("1.1_x", "Add feature")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=root, check=True)
    (root / "foo.txt").write_text("foo\n")
    subprocess.run(["git", "add", "foo.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "work"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
    return branch


def test_task_branch_is_merged_true_after_hand_merge(tmp_path):
    from snodo.infrastructure.worktree import task_branch_is_merged

    root = tmp_path / "proj"
    root.mkdir()
    _init_repo_on_main(root)
    branch = _make_task_branch_with_work(root)
    subprocess.run(["git", "merge", "-q", "--no-ff", "-m", "merge", branch], cwd=root, check=True)

    assert task_branch_is_merged(str(root), "1.1_x", "Add feature") is True


def test_task_branch_is_merged_false_when_not_merged(tmp_path):
    from snodo.infrastructure.worktree import task_branch_is_merged

    root = tmp_path / "proj"
    root.mkdir()
    _init_repo_on_main(root)
    _make_task_branch_with_work(root)

    assert task_branch_is_merged(str(root), "1.1_x", "Add feature") is False


def test_task_branch_is_merged_none_when_branch_missing(tmp_path):
    from snodo.infrastructure.worktree import task_branch_is_merged

    root = tmp_path / "proj"
    root.mkdir()
    _init_repo_on_main(root)

    assert task_branch_is_merged(str(root), "1.1_x", "Add feature") is None
