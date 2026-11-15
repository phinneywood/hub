# The Hub app

Source for Hub's new front, one app in place of the separate pages: Preact, `@preact/signals` and
TypeScript, built with Vite. Nothing else runs in the browser: no router library, CSS framework or UI kit.
The built files are committed in `hub/static/app/`, so the Mac that runs Hub and the deployer need no Node.

To change the app, on the Mac mini, from the repository root:

    tools/build-app

It keeps the pinned Node (`.node-version`) in `.tools/`, ignored by Git, then runs `npm ci --ignore-scripts`
and `npm run build` here. Elsewhere, with Node 24, run those two commands in this folder.
`npm run build` type-checks first (`npm run check`, which is `tsc --noEmit`) and stops on an error.

Commit `src/`, `package-lock.json` and the three built files in `hub/static/app/` (`app.js`, `app.css`,
`build.json`) together. `tests/test_app_page.py` fails if the built files and this source don't match.

## What the build writes, and what it leaves alone

The build writes exactly `app.js` and `app.css`, with fixed names, then `build.json`, the fingerprints
the test compares. It removes only those three before building. Everything else in `hub/static/app/` is
placed by hand and left alone: `fonts/` now, a service worker later. A new file or folder there must be
added to `BY_HAND` in the test and, for a folder, to the package data in `pyproject.toml`.

The page loads the two files itself:

    <link rel="stylesheet" href="/static/app/app.css">
    <div id="app"></div>
    <script type="module" src="/static/app/app.js"></script>

Hub's content policy is `default-src 'self'`, so the build inlines nothing: no style tags, no `data:`
addresses, no eval. Keep it that way: no CSS-in-JS, and refer to fonts and images by address.
In a stylesheet, `/fonts/Jost.ttf` means `hub/static/app/fonts/Jost.ttf`, and the build writes the
address as `/static/app/fonts/Jost.ttf`. For a file that isn't there it only warns and leaves the address
as written; the test then fails, as it does for any address with no file behind it. An image imported
from `src/` would be written out as a third built file (`app.svg`), which the test also refuses.

## Versions

Versions are pinned exactly. Update them on purpose, rebuild, and look at the page in Safari and Chrome
before committing. The lock file was made with `npm install --ignore-scripts --before=<date>`, so every
package in it, direct or not, was already published on that date; do the same when updating.

`package.json` overrides `lru-cache` (a build-time package Babel brings in through the Preact preset)
to 5.1.0, one patch behind the 5.1.1 Babel asks for. The lock file's checksum line for 5.1.1 happened to
contain a string the private word list refuses, and the hook is never bypassed. If a future update is
refused for the same reason, pick a neighbouring patch release or ask the owner to allow that exact line.
