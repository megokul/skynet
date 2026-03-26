Project: {project_name} ({project_type})
Working directory: {working_dir}

Task:
{milestone_text}

Implement this task completely by writing files directly in the working directory.
This is an implementation task, not a planning task.
{plan_section}
Requirements:
- Implement only the assigned milestone. Do not proactively complete future milestones just to make the project look finished.
- Keep changes cohesive: update only the files needed for this milestone and any directly impacted supporting files.
- If this milestone creates or changes the runnable entrypoint, update {run_contract_file} using this schema: {run_contract_schema}
- Prefer entrypoint file `{preferred_entrypoint}` unless an existing entrypoint already exists.
- If this milestone adds or changes behavior that should be validated, create or update tests for that behavior.
- If this milestone owns documentation, update README/run instructions only for the behavior it introduces.
- The entrypoint in {run_contract_file}, when touched by this milestone, MUST be the real application entrypoint. Do not replace it with a stub that only prints a message.
- Do not ask clarifying questions.
- Do NOT return architecture plans, checklists, or mermaid diagrams.
