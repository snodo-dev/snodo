from git import Repo

from snodo.validators.coverage_accountability import coverage_findings


def _repo(tmp_path, content):
    repo = Repo.init(tmp_path)
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
