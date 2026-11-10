# Next task

Hub is live as one private service, deployed by the Hub deployer in the home-ops repository
(`hub-deployer/hub_release.py`; see home-ops `docs/HUB-DEPLOYER.md`). The single-candidate
adoption described in docs/PRIVATE-ADOPTION.md is complete; that document is now history.

Release flow: work on a branch, get the owner's yes to merge to `main`, push `main`, run
`hub_release.py stage`, then the owner taps Deploy in Hub. The deployer backs up Hub's data,
switches versions, checks the running service and rolls back by itself on any failure.
`hub_release.py status` shows what is running, pending and what happened last.

Open owner decision: Hub's files and history still contain personal information (see AGENTS.md).
It stays private until the owner approves and completes a cleanup. Public hosting stays paused.

Start a task-specific session in this repository. Read AGENTS.md and docs/STATUS.md first.
