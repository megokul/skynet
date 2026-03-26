Project: {project_name} ({project_type})
Working directory: {working_dir}

Milestone task:
{milestone_text}

The previous implementation failed strict quality gates. Fix the code and update any needed files so all gates pass:
{failure_lines}

Requirements:
- Include a valid {run_contract_file}.
- Add runnable tests if missing (Python: tests/test_smoke.py).
- Ensure lint and tests pass.
- Return complete files only.

Execution instructions:
- Use coding tools to directly create or update files in the working directory.
- Do not ask clarifying questions; implement the fixes now.
- Print a short completion summary after file edits.
