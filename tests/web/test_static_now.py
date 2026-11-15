from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "snodo" / "web" / "static" / "now.js"


def test_now_view_registers_and_fetches_api() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "window.snodoViews.now" in source
    assert 'fetch("/api/now")' in source


def test_now_view_uses_safe_dom_apis() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "textContent" in source


def test_now_view_has_sections_and_fetch_error_handling() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "In flight" in source
    assert "Waiting on you" in source
    assert "The dashboard could not be reached." in source
    assert "catch (_)" in source


def test_now_view_uses_window_counters_and_freshness_controls() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for field in ("counters", "window_hours", "generated_at", "projects_active", "by_status"):
        assert field in source
    for label in ("Active projects", "Plans / Queues", "Waves", "Tasks", "Recons", "Commits"):
        assert f'"{label}"' in source
    assert '"—"' in source
    assert '"Refetch"' in source
    assert '`as of ${seconds}s ago`' in source


def test_now_tree_emits_hierarchical_rows_and_liveness_labels() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for row_class in ("now-project", "now-plan", "now-wave", "now-outside"):
        assert f'"{row_class}"' in source
    assert "`now-task ${statusClass(task.status)}`" in source
    assert '"Working"' in source
    assert '"Reporting, not progressing"' in source
    assert '"No recent signal"' in source


def test_now_tree_expansion_defaults_and_refresh_state() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const expandedRows = new Map();' in source
    assert 'expandedRows.has(key) ? expandedRows.get(key) : defaultExpanded' in source
    assert 'expandedRows.set(key, next)' in source
    assert 'button.setAttribute("aria-expanded", String(expanded))' in source
    assert 'button.setAttribute("aria-expanded", String(next))' in source
    assert 'tasks.every((task) => task.status === "completed")' in source
    assert '!allCompleted' in source
    assert '`${key}:outside`, false' in source
