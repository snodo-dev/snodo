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


def test_now_task_rows_are_buttons() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'element("button", undefined, `now-task-button' in source


def test_now_task_click_opens_accessible_panel() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'button.addEventListener("click"' in source
    assert 'element("aside", undefined, "now-task-panel")' in source
    assert 'panel.setAttribute("role", "complementary")' in source
    assert 'panel.setAttribute("aria-label"' in source


def test_now_task_panel_header_includes_details() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for detail in ("task.task_id", "task.status", "context.plan", "context.wave", "task.job && task.job.job_id"):
        assert detail in source


def test_now_task_panel_can_close_by_button_and_escape() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'element("button", "Close", "now-task-panel-close")' in source
    assert 'event.key === "Escape"' in source


def test_now_selected_task_survives_rerender() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "let selectedTaskKey = null" in source
    assert "selectedTaskKey === key" in source
    assert "if (selectedTaskKey && !selectedTaskDetails) selectedTaskKey = null" in source
    assert "currentContainer.replaceChildren(render(currentData, currentContainer))" in source


def test_now_selected_row_has_accessible_selection_state() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'button.setAttribute("aria-expanded", String(selectedTaskKey === key))' in source
    assert 'button.setAttribute("aria-pressed", String(selectedTaskKey === key))' in source


def test_now_panel_focuses_on_open_and_returns_to_row_on_close() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "close.focus()" in source
    assert "row.focus()" in source


def test_now_task_spec_fetch_encodes_query_values() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'fetch(url)' in source
    assert '`/api/now/spec?project=${encodeURIComponent(context.project)}&plan=${encodeURIComponent(context.plan)}&task=${encodeURIComponent(task.task_id)}`' in source


def test_now_task_spec_uses_renderer_and_preformatted_fallback() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'window.snodoMarkdown.render(markdown)' in source
    assert 'element("pre", undefined, "now-task-panel-plain")' in source
    assert 'pre.textContent = markdown' in source


def test_now_task_spec_messages_and_retry_are_present() -> None:
    source = SCRIPT.read_text(encoding="utf-8").replace("…", "…")
    for message in ("Loading spec…", "No spec on this machine for this task.", "This task ran outside a plan and has no spec.", "The spec could not be loaded.", 'element("button", "Retry"'):
        assert message in source


def test_now_task_spec_ignores_late_response_after_switch() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'selectedTaskKey !== key || !body.isConnected' in source


def test_now_task_spec_cache_survives_refresh() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'const specCache = new Map()' in source
    assert 'let entry = specCache.get(key)' in source
    assert 'if (entry && !retry)' in source
    assert 'specCache.set(key, entry)' in source
