# Workflow Orchestration

Use this skill for non-trivial engineering tasks with multiple steps, architecture decisions, or debugging loops.

## Operating Rules

- Start with a short written plan before changing code.
- Break work into small checkable steps.
- Re-plan if the first approach starts drifting or getting hacky.
- Prefer fixing root causes instead of stacking temporary patches.
- Keep changes small and local.

## Execution Pattern

1. Inspect the current system first.
2. Write down the intended steps.
3. Implement the smallest clean change.
4. Verify with logs, tests, or direct behavior checks.
5. Record any new lessons that should change the workflow next time.

## Verification Standard

- Do not call a task done without proving the behavior.
- Compare intended behavior to actual behavior.
- Check the real runtime path, not just static code.
- For operational changes, verify with the actual service or host state.

## Quality Bar

- Prefer boring and explicit over clever.
- Ask whether a simpler mechanism would be easier to maintain.
- Avoid broad refactors unless they clearly reduce complexity.
