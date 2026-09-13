"""`snodo cache clear` deletes the project's verdict cache (Fixes #246).

The first thing an operator does when they suspect a cache is delete it, so
the cache has a first-class command rather than a guessed path.
"""

from snodo.cli.commands import cache_cmd
from snodo.cli.json_output import EXIT_INTERNAL_ERROR, EXIT_PASS


def test_cache_clear_removes_the_project_cache(tmp_path, monkeypatch, capsys):
    cache_file = tmp_path / ".snodo" / "verdict_cache.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text('{"schema": 1, "entries": {}}', encoding="utf-8")
    monkeypatch.setattr(cache_cmd, "_resolve_cache_path", lambda: cache_file)

    assert cache_cmd.cache_clear(json=False) == EXIT_PASS

    assert not cache_file.exists()
    assert "Cleared verdict cache" in capsys.readouterr().out


def test_cache_clear_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cache_cmd, "_resolve_cache_path", lambda: tmp_path / "absent.json"
    )

    assert cache_cmd.cache_clear(json=False) == EXIT_PASS

    assert "No verdict cache to clear" in capsys.readouterr().out


def test_cache_clear_outside_a_project_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(cache_cmd, "_resolve_cache_path", lambda: None)

    assert cache_cmd.cache_clear(json=False) == EXIT_INTERNAL_ERROR

    assert "Not inside a snodo project" in capsys.readouterr().err
