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
    assert '"Last 24 hours"' in source
    assert '"Refreshes every ten seconds"' in source
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
    assert "if (selectedTaskKey && !selectedTaskDetails)" in source
    assert "now-flight-layout-open" in source
    assert "currentContainer.replaceChildren(render(currentData, currentContainer))" in source


def test_now_selected_row_has_accessible_selection_state() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'button.setAttribute("role", "option")' in source
    assert 'button.setAttribute("aria-selected", String(selectedTaskKey === key))' in source


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


def test_now_head_and_attention_strip_markup_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for text in ("Last 24 hours", "Now", "Refreshes every ten seconds", "Refetch", "Show task"):
        assert f'"{text}"' in source
    assert '"now-attention-strip"' in source
    assert '"now-attention-status"' in source
    assert '"now-attention-task"' in source
    assert '`and ${attentionTasks.length - 1} more`' in source
    assert '"failed", "blocked", "errored"' in source
    assert 'scrollIntoView({ behavior: "smooth", block: "center" })' in source


def test_now_attention_state_is_derived_from_current_data() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'hasAttentionCount ? " now-count-attention" : ""' in source
    assert 'if (attentionTasks.length)' in source
    assert 'attentionStatuses.has(String(task.status).toLowerCase())' in source


def test_now_fleet_and_plan_rows_render_summary_segments_chip_and_age() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"now-projects now-fleet"' in source
    assert '"now-project-name"' in source
    assert '"now-project-summary"' in source
    assert '"now-project-age"' in source
    assert '"now-plan-name"' in source
    assert '"now-plan-summary"' in source
    assert '"now-wave-segments"' in source
    assert '"now-plan-age"' in source
    assert 'segments.append(element("span", undefined, `now-wave-segment ${statusClass(task.status)}`))' in source
    assert 'segments.setAttribute("aria-label"' in source
    assert 'element("span", planState, `now-status now-plan-state ${statusClass(planState)}`)' in source
    assert 'plan.last_activity_at ? ageLabel(plan.last_activity_at)' in source
    assert 'groups.flatMap((group) => group.tasks || [])' in source


def test_now_plan_state_rules_and_default_expansion() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '["failed", "blocked", "errored"].includes(status)' in source
    assert '["in_progress", "in progress", "in-progress", "running"].includes(status)' in source
    assert 'taskStatuses.every((status) => status === "completed")' in source
    assert 'const planState = stateTask ? stateTask.status : "pending"' in source
    assert 'planKey, failedLike || runningLike, "now-plan-title"' in source
    assert 'expandedRows.has(key) ? expandedRows.get(key) : defaultExpanded' in source
    assert 'expandedRows.set(key, next)' in source


def test_now_plan_state_selection_preserves_contract_status_words() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const planState = stateTask ? stateTask.status : "pending"' in source
    assert '["in_progress", "in progress", "in-progress", "running"].includes(status)' in source
    assert 'String(task.status).toLowerCase() === "completed"' in source


def test_now_plan_state_selection_uses_pending_for_mixed_inactive_tasks() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const completed = tasksInPlan.length > 0 && taskStatuses.every((status) => status === "completed")' in source
    assert 'completed && String(task.status).toLowerCase() === "completed"' in source
    assert ': true' not in source[source.index("const stateTask ="):source.index("const planState =")]
    assert 'const planState = stateTask ? stateTask.status : "pending"' in source


def test_now_wave_and_task_rows_use_compact_list_markup() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'element("span", "Wave", "now-wave-kicker")' in source
    assert '`${group.total ?? tasks.length} tasks · ${statusSummary}`' in source
    assert 'taskList.setAttribute("role", "listbox")' in source
    assert 'id.title = task.task_id' in source
    assert 'now-task-icon-${iconStatus}' in source
    assert 'task.elapsed_seconds == null ? "—"' in source


def test_now_panel_has_facts_spec_and_conditional_copy_actions() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for label in ("Elapsed", "Wave", "Job", "Spec", "Copy task id", "Copy log path"):
        assert f'"{label}"' in source
    assert 'if (jobId) copyText("Copy log path"' in source
    assert 'navigator.clipboard.writeText(value)' in source
    assert 'feedback.textContent = value' in source
    assert '`${context.project}/.snodo/jobs/${jobId}/stdout.log`' in source


def test_now_manual_refetch_has_busy_failure_and_duplicate_request_guards() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"Refetching"' in source
    assert 'refetch.disabled = true' in source
    assert 'button.disabled = false' in source
    assert 'nowRequestInFlight' in source
    assert 'window.snodoViews.now(refetch.closest("[data-now-root]"), true)' in source
    assert 'Refresh failed; showing earlier data.' in source
    assert 'container.classList.add("now-refreshing")' in source
    assert 'fetch("/api/now")' in source


def test_now_background_refresh_does_not_enter_manual_busy_state() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    request = source[source.index("window.snodoViews.now = async function now"):source.index("if (!window.__snodoNowAgeTimer)")]
    manual_branch = request[request.index('if (manual && refetch)'):request.index("    try {")]

    assert "manual = false" in request
    assert 'window.snodoViews.now(refetch.closest("[data-now-root]"), true)' in source
    assert 'refetch.disabled = true' in manual_branch
    assert 'container.classList.add("now-refreshing")' in manual_branch
    assert 'age.textContent = "Refreshing data…"' in manual_branch
    assert 'fetch("/api/now")' not in manual_branch


def test_now_refetch_keeps_previous_view_until_successful_response() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    request = source[source.index("window.snodoViews.now = async function now"):source.index("if (!window.__snodoNowAgeTimer)")]
    success_path = request[request.index("const response = await fetch"):request.index("} catch (_)")]
    failure_path = request[request.index("} catch (_)"):request.index("} finally {")]

    assert 'await fetch("/api/now")' in success_path
    assert "container.replaceChildren(content)" in success_path
    assert "container.replaceChildren" not in failure_path
    assert 'message.textContent = "Refresh failed; showing earlier data."' in failure_path
    assert 'container.classList.add("now-refreshing")' in request
    assert 'container.classList.remove("now-refreshing")' in request
