# Code Quality Standards

Repository coding standards for runtime modules, handlers, executors, and supporting tests.

## Core Standards

1. Single responsibility per module.
   Modules should own one concern: policy resolution, state handling, transport/execution, persistence, or API routing.

2. Thin orchestration, thick helpers.
   Entry handlers and executors should coordinate work, not inline parsing, normalization, validation, and rendering logic.

3. Settings-driven behavior only.
   Runtime behavior must come from canonical config/settings paths, not handler-local defaults or ad hoc env parsing.

4. Explicit contracts between layers.
   Cross-boundary payloads must use named fields and normalization helpers instead of implicit string conventions.

5. Fail fast with typed errors.
   Invalid state, unsupported modes, and contract violations should produce classified failures, not silent fallback.

6. Meaningful names over comments.
   Prefer small, well-named functions and data structures. Comments should explain non-obvious invariants only.

7. No template-generated docstrings.
   Boilerplate `Purpose / How it works / Why this exists` blocks are noise. Use a short real docstring only when it adds information.

8. Extraction seams must be tested.
   When logic moves into a helper module, add focused tests for that seam instead of relying only on broad E2E coverage.

9. Keep runtime and test code aligned.
   Shared runtime behavior should have one implementation path. Test harnesses must reuse shared helpers instead of duplicating orchestration.

10. Preserve backward-compatible interfaces intentionally.
    If a refactor changes an internal implementation but external imports are already used by tests or runtime, keep compatibility aliases until callers are migrated.

## Structural Targets

- Avoid growing runtime modules past roughly `400-600` lines without extraction.
- If a module exceeds that size, split by concern before adding more policy branches.
- Prefer `dataclass` structures for grouped settings/state rather than parallel primitive fields.
- Keep transport-specific quoting/path/command building out of orchestration classes.

## Required Refactor Triggers

Refactor instead of patching when one of these is true:

- a module mixes config loading, state mutation, and transport execution
- multiple handlers duplicate the same normalization or fallback logic
- tests need large stubs because boundaries are implicit
- a function accumulates mode-specific branches for unrelated concerns
- a bug fix would otherwise add another local one-off condition

## Review Checklist

- Is the behavior sourced from canonical settings/config?
- Is the boundary payload explicit and validated?
- Can the logic be tested without instantiating the whole runtime path?
- Does the module own one concern?
- Did the change remove local branching/duplication instead of adding to it?
