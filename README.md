# OpenClaw Host Platform v0

This repository contains a simple first version of an internal hosting platform for running one Firecracker microVM per employee on a single DigitalOcean Droplet.

The implementation is intentionally boring:

- one immutable base guest image
- one per-user writable root filesystem cloned with filesystem-level copy-on-write (`cp --reflink=always`)
- one private bridge for guest networking
- one small host-side control CLI for lifecycle and rollback
- systemd-managed Firecracker processes

Start with:

- [docs/architecture.md](/Users/franciszhan/Documents/GitHub/openclaw-server/docs/architecture.md)
- [docs/operations.md](/Users/franciszhan/Documents/GitHub/openclaw-server/docs/operations.md)
- [docs/hardening.md](/Users/franciszhan/Documents/GitHub/openclaw-server/docs/hardening.md)

Core entrypoints:

- [scripts/openclaw-hostctl](/Users/franciszhan/Documents/GitHub/openclaw-server/scripts/openclaw-hostctl)
- [bootstrap/install-host.sh](/Users/franciszhan/Documents/GitHub/openclaw-server/bootstrap/install-host.sh)
- [guest/build-base-image.sh](/Users/franciszhan/Documents/GitHub/openclaw-server/guest/build-base-image.sh)

Validation:

```bash
make test
make validate
```
