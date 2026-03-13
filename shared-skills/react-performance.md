# React Performance

Use this skill when building or reviewing React or Next.js code that may become slow or heavy.

## Highest-Value Defaults

- Start independent async work early and await it late.
- Avoid request waterfalls.
- Import directly instead of relying on large barrel files.
- Defer heavy client code until it is actually needed.

## Rendering Rules

- Use `startTransition` for non-urgent updates.
- Avoid storing derived state when it can be computed during render.
- Use refs for transient values that should not trigger re-renders.
- Memoize only when it removes real work.

## Bundle Rules

- Keep large libraries out of the initial path when possible.
- Dynamically load expensive components.
- Do not ship server-only work to the client.

## Practical Review Questions

- Is any data fetching serialized unnecessarily?
- Is any big dependency loaded before the user needs it?
- Are re-renders coming from the wrong state boundary?
- Is expensive work happening on every render without need?
