# Shared Skills Bundle

This directory is the repo-managed seed bundle for company-approved skills copied into each guest at:

- `/opt/openclaw/skills/company`

Use it as the source for `shared_skills_dir`.

Typical sync step on the host:

```bash
sudo rsync -a /opt/openclaw-server/shared-skills/ /var/lib/openclaw/shared-skills/
```

Rebuild the base image after materially changing these files so new guests inherit the updated bundle.
