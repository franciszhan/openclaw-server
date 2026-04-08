# Lessons

- Do not let tests or temp configs write into hardcoded global runtime paths. Any persistent host-side state, especially auth inventories like the Google broker root, must come from `HostConfig` so tests stay sandboxed.
- Do not hide destructive auth-state refreshes inside setup helpers. Directory/user ensure paths must be side-effect free with respect to live credentials; explicit reconcile/refresh paths should own pruning.
