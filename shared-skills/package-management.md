# Package Management

Use the guest package helpers instead of raw elevated commands.

- To find Debian packages, run `pkg-search <query>`.
- To install Debian packages, run `pkg-install <package> [package...]`.
- Do not use `sudo apt-get install` directly from Slack sessions.
- Keep installs targeted and explain why each package is needed.
- Prefer packages from the default Debian repositories.

Examples:

```bash
pkg-search ripgrep
pkg-install ripgrep jq
```
