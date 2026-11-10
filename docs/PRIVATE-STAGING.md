# Manual private staging releases

The hosted installed-package check now retains `release-<full commit SHA>` for
14 days after a successful run. Its closed contents are `source.tar`, the sdist,
the wheel, reviewed `macos-arm64-wheelhouse.tar` and `verification.json`, which binds every artifact digest to the exact
source commit. Only committed source and the hash-reviewed public dependency wheels enter this bundle. No environment, live
catalog, private binding, test output, virtual environment or credential is uploaded.
The artifact is kept in this existing private repository with its existing access.

The separate homelab-ops `deployment/stage_hub.py` consumer checks private-repository,
workflow, main-push, exact-SHA and completed-success metadata before downloading.
It verifies hashes and wheel bytes, installs into a fresh isolated environment and
repeats all guarded synthetic package tests before atomically changing its own
staging receipt. Its root must first be explicitly initialized as isolated staging.
No service uses that receipt, and no server or background job is launched.

This is not a production deployment. The preserved combined Hub still owns its
existing private access and Preschool continuity. No launcher, runtime binding,
credential, listener, ingress, schedule, catalog or account privilege changes.
Independent Preschool access, consistent private backup/restore, exact allowed
startup effects, current journals and a separately approved cutover remain gates.
A successful staging install is not live HTTP, device, source-system or iPhone
acceptance. Rollback here restores source selection, not external effects or data.

See OFFLINE-DEPENDENCIES.md for the reviewed hash manifest, deterministic dependency
archive, proposal workflow and fully offline fresh Mini installation. No existing
virtual environment or online index is required on the Mini.
