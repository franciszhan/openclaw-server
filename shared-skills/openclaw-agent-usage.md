# OpenClaw Agent Usage

Use this skill as an operating guide for OpenClaw agents. Apply these defaults unless the user gives a stronger instruction.

## Repeat What Worked

- When you complete a complicated task successfully, propose capturing the workflow in a reusable form.
- If the workflow is likely to repeat, summarize the working process into a checklist, template, or draft skill for review.
- Prefer repeatable operating patterns over one-off hero runs.

Use language like:

```text
This worked. Summarize the process and turn it into a repeatable skill if it is likely to happen again.
```

## Parallelize Deliberately

- When tasks are independent, split them across subagents and run them in parallel.
- Use subagents for research, log inspection, file reading, and independent implementation tracks.
- Keep one clear owner for final synthesis, integration, and verification.

Use this pattern:

```text
Spin up a subagent for each independent task, then consolidate the results into one verified answer.
```

## Pick The Right Model

- Use `gpt-5.2-instant` for quick, low-risk, uncomplicated work.
- Use `gpt-5.4` for most agentic coding, debugging, and normal research.
- Use `gpt-5.4-pro` for deeper research, niche sources, or more exhaustive synthesis.

## Match Thinking Level To The Task

- Use `low` or `medium` thinking for most work.
- Use `high` thinking for multi-step tasks, architecture changes, delicate debugging, or expensive mistakes.
- Do not default to maximum thinking for simple edits.

## Prompt Like An Operator

- Restate the goal, constraints, and success criteria clearly before doing substantial work.
- Optimize for the priority the user implies or states: speed, accuracy, safety, depth, or maintainability.
- Verify results instead of stopping at implementation.
- If the task is risky, produce a rollback or safety plan before making changes.

Use language like:

```text
Handle this end to end. Keep it maintainable. Verify the result before stopping.
```

## Good Defaults

- Inspect the current system before proposing or making changes.
- After long runs, produce a concise summary of what changed, what was verified, and what remains risky.
- Surface useful repeated patterns for later promotion into approved shared skills.
- Use the smallest model and lowest thinking level that still fits the task.
