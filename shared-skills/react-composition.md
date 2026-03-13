# React Composition

Use this skill when components are becoming hard to extend or are accumulating boolean props.

## Preferred Patterns

- Prefer composition over mode flags.
- Use explicit variant components instead of many booleans.
- Use compound components when multiple pieces share state.
- Keep state management behind a provider or narrow interface.

## Warning Signs

- One component has many boolean props.
- Sibling components need the same state but manage it separately.
- Consumers need to know internal implementation details.
- Render props are being used where plain children would be simpler.

## Default Moves

- Split large mode-heavy components into smaller explicit pieces.
- Lift shared state to the nearest provider boundary.
- Keep component APIs narrow and predictable.
- Optimize for maintainability over one-component cleverness.
