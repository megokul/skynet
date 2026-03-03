"""Deterministic bot conversation E2E with local git push verification."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from helpers import make_callback_update, make_context, make_message_update

from bot.handlers.greeting import greeting_handler
from bot.handlers.project import (
    AWAITING_PROJECT_NAME,
    AWAITING_PROJECT_TYPE,
    GATHERING_REQUIREMENTS,
    REVIEWING_PLAN,
    _NAME_KEY,
    _PLAN_KEY,
    _TYPE_KEY,
    approve_plan,
    ask_project_name,
    receive_project_name,
    receive_project_type,
    requirements_done_handler,
)
from bot.handlers.coding import (
    _ACTIVE_LOOP_KEY,
    _CODING_PID_KEY,
    _MS_DECISION_KEY,
    _MS_EVENT_KEY,
    coding_github_choice_handler,
    run_project_handler,
    start_coding_handler,
)
from bot.keyboards import (
    CB_CODING_GITHUB_YES,
    CB_PLAN_APPROVE,
    CB_REQUIREMENTS_DONE,
    CB_RUN_PROJECT,
    CB_START_CODING,
    CB_START_PROJECT,
)
from bot.state import KEY_DB, KEY_ROUTER
from db.schema import init_db
from db.store import get_project, list_tasks


def _callbacks(markup) -> list[list[str]]:
    return [[btn.callback_data for btn in row] for row in markup.inline_keyboard]


def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def _worker_ok(returncode: int = 0, stdout: str = "", stderr: str = "") -> dict:
    return {
        "status": "ok",
        "result": {
            "returncode": int(returncode),
            "stdout": stdout,
            "stderr": stderr,
        },
    }


@pytest.mark.asyncio
async def test_conversation_e2e_generates_code_and_pushes_local_remote(tmp_path: Path):
    if not shutil.which("git"):
        pytest.skip("git is required for this E2E test.")

    db = await init_db(":memory:")
    user_id = 72
    chat_id = 220
    project_name = "Repo Push App"
    slug = "repo-push-app"

    milestones = ["Implement the app entry script"]
    fake_plan = (
        "**Repo Push App — Project Plan**\n"
        "**Overview:** Build a tiny Python script.\n"
        "**Milestones:**\n"
        "  1. Implement the app entry script"
    )

    project_dir = tmp_path / slug
    project_file = project_dir / f"{slug}.py"
    remote_dir = tmp_path / f"{slug}.git"

    fake_router = MagicMock()
    fake_router.chat = AsyncMock(return_value=MagicMock(text=fake_plan))

    bot_data = {KEY_DB: db, KEY_ROUTER: fake_router}
    app = MagicMock()
    app.bot_data = bot_data
    sent_markups: list = []

    async def _send(_cid, _text, **kwargs):
        if "reply_markup" in kwargs:
            sent_markups.append(kwargs["reply_markup"])

    app.bot.send_message = AsyncMock(side_effect=_send)

    def make_ctx(*, extra_user_data=None):
        ctx = make_context(
            user_data={} if extra_user_data is None else extra_user_data,
            bot_data=bot_data,
        )
        ctx.application = app
        return ctx

    async def fake_send_action(action, params, **_kwargs):
        if action == "create_directory":
            Path(params["directory"]).mkdir(parents=True, exist_ok=True)
            return _worker_ok(0, f"Created {params['directory']}", "")

        if action == "git_init":
            cwd = params["working_dir"]
            rc, out, err = _run(["git", "init"], cwd=cwd)
            if rc == 0:
                _run(["git", "config", "user.email", "ci@example.com"], cwd=cwd)
                _run(["git", "config", "user.name", "SKYNET CI"], cwd=cwd)
                _run(["git", "branch", "-M", "main"], cwd=cwd)
            return _worker_ok(rc, out, err)

        if action == "file_write":
            filepath = Path(params["file"])
            filepath.parent.mkdir(parents=True, exist_ok=True)
            content = str(params.get("content", ""))
            filepath.write_text(content, encoding="utf-8")
            return _worker_ok(0, f"Wrote {len(content)} bytes to {filepath}", "")

        if action == "file_read":
            filepath = Path(params["file"])
            if not filepath.exists():
                return _worker_ok(1, "", f"File not found: {filepath}")
            content = filepath.read_text(encoding="utf-8")
            result = _worker_ok(0, content, "")
            result["result"]["content"] = content
            return result

        if action == "git_add_all":
            rc, out, err = _run(["git", "add", "-A"], cwd=params["working_dir"])
            return _worker_ok(rc, out, err)

        if action == "git_commit":
            rc, out, err = _run(
                ["git", "commit", "-m", params["message"]],
                cwd=params["working_dir"],
            )
            return _worker_ok(rc, out, err)

        if action == "gh_create_repo":
            cwd = params["working_dir"]
            repo_name = params["repo_name"]
            if not remote_dir.exists():
                rc, out, err = _run(["git", "init", "--bare", str(remote_dir)])
                if rc != 0:
                    return _worker_ok(rc, out, err)
            _run(["git", "remote", "remove", "origin"], cwd=cwd)
            rc, out, err = _run(["git", "remote", "add", "origin", str(remote_dir)], cwd=cwd)
            if rc != 0:
                return _worker_ok(rc, out, err)
            rc, out, err = _run(["git", "push", "-u", "origin", "main"], cwd=cwd)
            fake_url = f"https://github.com/local/{repo_name}"
            stdout = "\n".join(p for p in (out.strip(), fake_url) if p)
            return _worker_ok(rc, stdout, err)

        if action == "run_coding_agent":
            wd = Path(params["working_dir"])
            wd.mkdir(parents=True, exist_ok=True)
            script = wd / f"{wd.name}.py"
            tests_dir = wd / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            smoke_test = tests_dir / "test_smoke.py"
            run_manifest = wd / "skynet_run.json"
            script.write_text(
                textwrap.dedent(
                    f"""\
                    def main():
                        print("REPO_PUSH_E2E_OK")
                        return 0

                    if __name__ == "__main__":
                        raise SystemExit(main())
                    """
                ),
                encoding="utf-8",
            )
            smoke_test.write_text(
                "def test_smoke():\n"
                "    assert True\n",
                encoding="utf-8",
            )
            run_manifest.write_text(
                json.dumps(
                    {
                        "interpreter": "python",
                        "entrypoint": f"{wd.name}.py",
                        "args": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = _worker_ok(
                0,
                f"Wrote 3 file(s): {wd.name}.py, skynet_run.json, tests/test_smoke.py",
                "",
            )
            result["result"]["files_written"] = [
                f"{wd.name}.py",
                "skynet_run.json",
                "tests/test_smoke.py",
            ]
            return result

        if action == "list_directory":
            wd_path = Path(params["directory"])
            if not wd_path.is_dir():
                return _worker_ok(1, "", "directory not found")
            lines: list[str] = []
            recursive = bool(params.get("recursive"))
            if recursive:
                for entry in sorted(wd_path.rglob("*"), key=lambda p: str(p).lower()):
                    rel = entry.relative_to(wd_path).as_posix()
                    if entry.is_dir():
                        lines.append(f"[DIR] {rel}/")
                    else:
                        lines.append(f"{rel}  ({entry.stat().st_size} bytes)")
            else:
                for entry in sorted(wd_path.iterdir(), key=lambda p: p.name.lower()):
                    if entry.is_dir():
                        lines.append(f"[DIR] {entry.name}/")
                    else:
                        lines.append(f"{entry.name}  ({entry.stat().st_size} bytes)")
            return _worker_ok(0, "\n".join(lines), "")

        if action == "exec_command":
            cmd = str(params["command"])
            wd  = params["working_dir"]

            parts = cmd.split()
            rc, out, err = _run([sys.executable, *parts[1:]], cwd=wd)
            return _worker_ok(rc, out, err)

        return _worker_ok(0, "", "")

    user_data: dict = {}

    # STEP 1: greeting
    upd = make_message_update("hi", user_id=user_id)
    await greeting_handler(upd, make_ctx())
    assert CB_START_PROJECT in sum(_callbacks(upd.message.reply_text.call_args.kwargs["reply_markup"]), [])

    # STEP 2: start project
    upd = make_callback_update(CB_START_PROJECT, user_id=user_id)
    assert await ask_project_name(upd, make_ctx()) == AWAITING_PROJECT_NAME

    # STEP 3: name
    upd = make_message_update(project_name, user_id=user_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await receive_project_name(upd, ctx) == AWAITING_PROJECT_TYPE
    user_data.update(ctx.user_data)
    assert user_data[_NAME_KEY] == project_name

    # STEP 4: type
    upd = make_callback_update("type:python_app", user_id=user_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await receive_project_type(upd, ctx) == GATHERING_REQUIREMENTS
    user_data.update(ctx.user_data)
    assert user_data[_TYPE_KEY] == "Python App"

    # STEP 5: generate plan
    upd = make_callback_update(CB_REQUIREMENTS_DONE, user_id=user_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await requirements_done_handler(upd, ctx) == REVIEWING_PLAN
    user_data.update(ctx.user_data)
    assert user_data[_PLAN_KEY] == fake_plan

    # STEP 6: approve plan
    upd = make_callback_update(CB_PLAN_APPROVE, user_id=user_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await approve_plan(upd, ctx) == ConversationHandler.END
    user_data.update(ctx.user_data)
    project_id = user_data["last_project_id"]
    project_row = await get_project(db, project_id)
    assert project_row is not None

    # STEP 7: start coding
    upd = make_callback_update(CB_START_CODING, user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data={"last_project_id": project_id})
    await start_coding_handler(upd, ctx)
    assert ctx.user_data[_CODING_PID_KEY] == project_id

    # STEP 8: choose GitHub path and auto-approve milestone
    loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    decision_key = _MS_DECISION_KEY.format(uid=user_id)

    async def auto_approve():
        for _ in range(400):
            if event_key in bot_data:
                bot_data[decision_key] = "approve"
                bot_data[event_key].set()
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Milestone approval event was never created.")

    approve_task = asyncio.create_task(auto_approve())
    upd = make_callback_update(CB_CODING_GITHUB_YES, user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data={_CODING_PID_KEY: project_id})

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=milestones)),
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=fake_send_action)),
        patch("bot.handlers.coding.cfg") as mock_cfg,
    ):
        mock_cfg.WORKER_PROJECTS_DIR = str(tmp_path)
        await coding_github_choice_handler(upd, ctx)
        loop_task = bot_data.get(loop_key)
        assert loop_task is not None, "Coding loop task was not started."
        await asyncio.wait_for(loop_task, timeout=30)
    await approve_task

    # Verify generated code and pushed branch.
    assert project_dir.is_dir()
    assert project_file.is_file()
    assert "REPO_PUSH_E2E_OK" in project_file.read_text(encoding="utf-8")
    assert remote_dir.is_dir()
    rc, out, _err = _run(["git", "--git-dir", str(remote_dir), "rev-parse", "--verify", "refs/heads/main"])
    assert rc == 0, f"Expected refs/heads/main in bare remote, got rc={rc}, out={out!r}"

    tasks = await list_tasks(db, project_id=project_id)
    assert tasks and all(t["status"] == "done" for t in tasks), f"tasks={tasks}"
    assert bot_data.get(f"run_project_{user_id}") == project_id
    assert any(CB_RUN_PROJECT in sum(_callbacks(m), []) for m in sent_markups), "Run Project CTA missing."

    # STEP 9: run project
    upd = make_callback_update(CB_RUN_PROJECT, user_id=user_id, chat_id=chat_id)
    ctx = make_context(bot_data=dict(bot_data))
    with (
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=fake_send_action)),
        patch("bot.handlers.coding.cfg") as mock_cfg,
    ):
        mock_cfg.WORKER_PROJECTS_DIR = str(tmp_path)
        await run_project_handler(upd, ctx)

    all_replies = " ".join(
        str(c.args[0] if c.args else "")
        for c in upd.callback_query.message.reply_text.call_args_list
    )
    assert "REPO_PUSH_E2E_OK" in all_replies
    assert "exit 0" in all_replies or "✅" in all_replies

    await db.close()
