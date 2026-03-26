Project: {project_name} ({project_type})
Working directory: {working_dir}

STRICT RECOVERY MODE:
Previous coding attempts exited successfully but produced no files.
Write files now. Do not ask clarifying questions.

Milestone task:
{milestone_text}

Required outputs:
1) {entrypoint}
- Must be runnable with `{interpreter} {entrypoint}`
{marker_requirement}- Exit code must be 0.

2) {run_contract_file} with:
{{
  "interpreter": "{interpreter}",
  "entrypoint": "{entrypoint}",
  "args": []
}}

3) {tests_file}
- Must execute the entrypoint and assert exit code 0.

Output only fenced code blocks where each fence tag is the filename.
