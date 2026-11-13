# The Ask page

Source for Hub's `/ask` chat page: React, assistant-ui primitives and the AI SDK's chat transport.
It is the only Hub page with a build step. The built files are committed in `hub/static/ask/`, so
the Mac that runs Hub and the deployer need no Node.

To change the page, on the Mac mini, from the repository root:

    tools/build-ask-page

It keeps the pinned Node (`.node-version`) in `.tools/`, ignored by Git, then runs `npm ci --ignore-scripts`
and `npm run build` here. Elsewhere, with Node 24, run those two commands in this folder.

Commit `src/`, `package-lock.json` and the three files in `hub/static/ask/` together.
`tests/test_ask_page.py` fails if the built files and this source don't match.

Versions are pinned exactly. Update them on purpose, rebuild, and run the browser check
(`web/ask/check/README.md`) before committing.

React is pinned at 19.3.0, one patch behind the newest at the time. The lock file's checksum line for
19.3.1 happened to contain a string the private word list refuses, and the hook is never bypassed. If a
future update is refused for the same reason, pick a neighbouring patch release or ask the owner to
allow that exact line.
