from git import Repo

from snodo.tools.git import open_repo
from snodo.validators.coverage_accountability import coverage_findings


def _repo(tmp_path, content):
    Repo.init(tmp_path).close()
    repo = open_repo(str(tmp_path))
    path = tmp_path / "test_contract.py"
    path.write_text(content)
    repo.index.add([str(path)])
    base = repo.index.commit("base")
    return repo, path, base


def test_missing_test_case_is_operator_finding(tmp_path):
    repo, path, base = _repo(tmp_path, "def test_kept():\n    pass\n\ndef test_removed():\n    pass\n")
    path.write_text("def test_kept():\n    assert True\n")
    repo.index.add([str(path)])
    repo.index.commit("remove")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Add assertion", working_directory=str(tmp_path))
    assert any("test_removed" in f.justification and "test_contract.py" in f.justification and f.severity == "warn" for f in findings)


def test_requested_removal_is_expected(tmp_path):
    repo, path, base = _repo(tmp_path, "def test_removed():\n    pass\n")
    path.write_text("def test_new_contract():\n    pass\n")
    repo.index.add([str(path)])
    repo.index.commit("replace")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Remove test_removed; replace it with test_new_contract", working_directory=str(tmp_path))
    assert not findings


def test_rewritten_test_case_is_retained(tmp_path):
    repo, path, base = _repo(tmp_path, "def test_contract():\n    pass\n")
    path.write_text("def test_contract():\n    assert 1 == 1\n")
    repo.index.add([str(path)])
    repo.index.commit("rewrite")
    assert not coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Rewrite test_contract", working_directory=str(tmp_path))


def test_substantial_explanation_loss_is_finding(tmp_path):
    prose = "".join(f"# Contract explanation line {i}\n" for i in range(24))
    repo, path, base = _repo(tmp_path, prose + "def helper():\n    pass\n")
    path.write_text("def helper():\n    pass\n")
    repo.index.add([str(path)])
    repo.index.commit("strip explanation")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Refactor helper implementation", working_directory=str(tmp_path))
    assert any("explanatory" in f.justification.lower() and "test_contract.py" in f.justification for f in findings)


def test_deleted_test_file_is_operator_finding(tmp_path):
    repo, path, base = _repo(tmp_path, "def test_gone():\n    pass\n")
    path.unlink()
    repo.index.remove(["test_contract.py"])
    repo.index.commit("delete test file")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Unrelated work", working_directory=str(tmp_path))
    assert any("test_contract.py" in f.justification and "test_gone" in f.justification for f in findings)


def test_specified_deleted_test_file_is_expected(tmp_path):
    repo, path, base = _repo(tmp_path, "def test_gone():\n    pass\n")
    path.unlink()
    repo.index.remove(["test_contract.py"])
    repo.index.commit("delete test file")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Delete test_contract.py and test_gone", working_directory=str(tmp_path))
    assert not any("test_gone" in f.justification for f in findings)


def test_renamed_file_with_cases_retained_has_no_finding(tmp_path):
    repo, path, base = _repo(tmp_path, "def test_kept():\n    pass\n")
    renamed = tmp_path / "renamed_contract.py"
    path.rename(renamed)
    repo.index.remove(["test_contract.py"])
    repo.index.add([str(renamed)])
    repo.index.commit("rename test file")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Rename the test module", working_directory=str(tmp_path))
    assert not findings


def test_docstring_prose_loss_is_finding(tmp_path):
    prose = "\n".join(f"    Contract explanation prose line {i}." for i in range(24))
    repo, path, base = _repo(tmp_path, f'def helper():\n    """\n{prose}\n    """\n    pass\n')
    path.write_text("def helper():\n    pass\n")
    repo.index.add([str(path)])
    repo.index.commit("strip docstring")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Refactor helper implementation", working_directory=str(tmp_path))
    assert any("explanatory" in f.justification.lower() and "test_contract.py" in f.justification for f in findings)


def test_code_rewrite_preserving_docstring_and_comments_has_no_finding(tmp_path):
    content = 'def helper():\n    """' + "\n".join(f"    Contract explanation prose line {i}." for i in range(16)) + '\n    """\n    # Keep this explanation.\n    return 1\n'
    repo, path, base = _repo(tmp_path, content)
    path.write_text(content.replace("return 1", "return 2"))
    repo.index.add([str(path)])
    repo.index.commit("rewrite implementation")
    findings = coverage_findings(base_ref=base.hexsha, head_ref="HEAD", task_spec="Change helper implementation", working_directory=str(tmp_path))
    assert not findings
