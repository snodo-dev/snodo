from pathlib import Path


from snodo.web.roots import load_roots, save_roots


def test_missing_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    assert load_roots() == []


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    roots = [tmp_path / "one", tmp_path / "two"]
    save_roots(roots)
    assert load_roots() == [str(root.resolve()) for root in roots]


def test_save_expands_home_and_removes_duplicates(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SNODO_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    save_roots(["~/projects", tmp_path / "projects", "~/other"])
    assert load_roots() == [str((tmp_path / "projects").resolve()), str((tmp_path / "other").resolve())]


def test_corrupt_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    config_dir = Path(tmp_path / "home")
    config_dir.mkdir()
    (config_dir / "web.yml").write_text("roots: [unterminated", encoding="utf-8")
    assert load_roots() == []


def test_save_does_not_modify_config_and_is_noop_for_same_roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SNODO_HOME", str(home))
    home.mkdir()
    config = home / "config.yml"
    original = b"# hand-written comment\nmodel: example\n"
    config.write_bytes(original)
    roots = [tmp_path / "projects"]
    save_roots(roots)
    web_file = home / "web.yml"
    first_stat = web_file.stat()
    first_content = web_file.read_bytes()
    save_roots(roots)
    assert config.read_bytes() == original
    assert web_file.read_bytes() == first_content
    assert web_file.stat().st_ino == first_stat.st_ino


def test_save_replaces_atomically_without_leaving_temporary_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SNODO_HOME", str(home))
    save_roots([tmp_path / "project"])
    assert (home / "web.yml").is_file()
    assert list(home.iterdir()) == [home / "web.yml"]
