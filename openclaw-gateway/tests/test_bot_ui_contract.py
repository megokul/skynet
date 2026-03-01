"""Strict keyboard layout contract tests for bot conversation UX."""

from __future__ import annotations

from bot.keyboards import (
    CB_CHAT,
    CB_CODING_GITHUB_SKIP,
    CB_CODING_GITHUB_YES,
    CB_CODING_RETRY_PREFIX,
    CB_MAIN_MENU,
    CB_MILESTONE_APPROVE,
    CB_MILESTONE_SKIP,
    CB_MILESTONE_STOP,
    CB_MY_PROJECTS,
    CB_PLAN_APPROVE,
    CB_PLAN_CHANGES,
    CB_REMINDER,
    CB_RUN_PROJECT,
    CB_START_CODING,
    CB_START_PROJECT,
    CB_TYPE_API,
    CB_TYPE_BOT,
    CB_TYPE_CLI,
    CB_TYPE_DATA,
    CB_TYPE_DESKTOP,
    CB_TYPE_LIBRARY,
    CB_TYPE_MOBILE,
    CB_TYPE_OTHER,
    CB_TYPE_PYTHON,
    CB_TYPE_WEB,
    CB_WEATHER,
    CB_WEB_SEARCH,
    coding_github_setup,
    main_menu,
    milestone_review,
    plan_review,
    project_type,
    retry_coding,
    run_project,
    start_coding,
)


def _callbacks(markup) -> list[list[str]]:
    return [[btn.callback_data for btn in row] for row in markup.inline_keyboard]


def test_main_menu_layout_contract():
    assert _callbacks(main_menu()) == [
        [CB_START_PROJECT],
        [CB_WEATHER, CB_REMINDER],
        [CB_WEB_SEARCH, CB_CHAT],
    ]


def test_project_type_layout_contract():
    assert _callbacks(project_type()) == [
        [CB_TYPE_WEB, CB_TYPE_PYTHON],
        [CB_TYPE_MOBILE, CB_TYPE_DESKTOP],
        [CB_TYPE_CLI, CB_TYPE_API],
        [CB_TYPE_LIBRARY, CB_TYPE_DATA],
        [CB_TYPE_BOT, CB_TYPE_OTHER],
    ]


def test_plan_review_layout_contract():
    assert _callbacks(plan_review()) == [[CB_PLAN_APPROVE, CB_PLAN_CHANGES]]


def test_start_coding_layout_contract():
    assert _callbacks(start_coding()) == [
        [CB_START_CODING],
        [CB_MY_PROJECTS, CB_MAIN_MENU],
    ]


def test_coding_github_setup_layout_contract():
    assert _callbacks(coding_github_setup()) == [
        [CB_CODING_GITHUB_YES],
        [CB_CODING_GITHUB_SKIP],
    ]


def test_milestone_review_layout_contract():
    rows = _callbacks(milestone_review())
    assert rows == [
        [CB_MILESTONE_APPROVE, CB_MILESTONE_SKIP],
        [CB_MILESTONE_STOP],
    ]
    # Destructive action must be isolated at the end.
    assert rows[-1] == [CB_MILESTONE_STOP]


def test_run_project_layout_contract():
    assert _callbacks(run_project()) == [
        [CB_RUN_PROJECT],
        [CB_MAIN_MENU],
    ]


def test_retry_coding_layout_contract():
    project_id = "proj_123"
    rows = _callbacks(retry_coding(project_id))
    assert rows == [
        [f"{CB_CODING_RETRY_PREFIX}{project_id}"],
        [CB_MY_PROJECTS, CB_MAIN_MENU],
    ]
