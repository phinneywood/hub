# The Dev page

`/dev` (All tools → Dev) is the owner's one place to see how they develop on the Mac mini and the live
state of every project. It keeps nothing: each visit reads the projects folder, Git and (when set) tmux afresh. It is
read-only and behind the same owner sign-in as every tool. It is listed under All tools but has no card on
Today (`"today": False` on the tool). Code: `hub/tools/dev/`.

| Section | Read from |
| --- | --- |
| How you work | The numbered list under "How the owner works" and the table under "Keys" (`| Key | What |`) in the projects folder's `README.md` |
| In progress | Only when `tmux_session` is set: `tmux list-windows` for that session; "Claude running" means a `claude` process under the window's pane |
| Projects | One card per pointer under "Other Repos" in the projects folder's `AGENTS.md` (name, the text in brackets, then the `~/` path); Git branch, last commit, other local branches, uncommitted changes, worktrees. For `hub`, the running version and any pending deploy from the deployer's notes (`hub/engine/release.py`) |
| Waiting on you | Only when `manager_brief` is set: the numbered list under "## Waiting on the owner" in that private file |

Git is only asked: `rev-parse`, `log`, `for-each-ref`, `status` and `worktree list`, each with a
5-second limit and `GIT_OPTIONAL_LOCKS=0`, so not even the index is refreshed. Nothing fetches or pulls.
When tmux can't be reached from Hub's process the section says "Not available"; a missing setting
says "Not configured"; a missing repository says so on its card.

## Settings

In the private `household.json` (data directory), section `dev`. Placeholder values:

```json
{
  "dev": {
    "projects": "~/path/to/projects"
  }
}
```

`~` is expanded at run time. `projects` is the folder whose `README.md` and `AGENTS.md` the page reads
(the older key `home_repo` still counts). Optional: `tmux_session` (a tmux session to list) and
`manager_brief` (a private Markdown file with a "Waiting on the owner" list); each section is left out
when its setting is.
