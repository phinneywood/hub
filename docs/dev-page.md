# The Dev page

`/dev` (All tools → Dev) is the owner's one place to see how they develop on the Mac mini and the live
state of every project. It keeps nothing: each visit reads the home repo, tmux and Git afresh. It is
read-only and behind the same owner sign-in as every tool. Code: `hub/tools/dev/`.

| Section | Read from |
| --- | --- |
| How you work | The numbered list under "How the owner works" in the home repo's `README.md`, plus a keys table in the template |
| In progress | `tmux list-windows` for the Manager's session; "Claude running" means a `claude` process under the window's pane |
| Projects | One card per pointer under "Other Repos" in the home repo's `AGENTS.md` (name, the text in brackets, then the `~/` path); Git branch, last commit, other local branches, uncommitted changes, worktrees. For `hub`, the running version and any pending deploy from the deployer's notes (`hub/engine/release.py`) |
| Waiting on you | The numbered list under "## Waiting on the owner" in the Manager's private brief |

Git is only asked: `rev-parse`, `log`, `for-each-ref`, `status` and `worktree list`, each with a
5-second limit and `GIT_OPTIONAL_LOCKS=0`, so not even the index is refreshed. Nothing fetches or pulls.
When tmux can't be reached from Hub's process the section says "Not available"; a missing setting
says "Not configured"; a missing repository says so on its card.

## Settings

In the private `household.json` (data directory), section `dev`. Placeholder values:

```json
{
  "dev": {
    "home_repo": "~/path/to/home-repo",
    "manager_brief": "~/path/to/private/MANAGER.md",
    "tmux_session": "manager"
  }
}
```

`~` is expanded at run time. `tmux_session` is optional (default `manager`).
