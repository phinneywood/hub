// Writes hub/static/ask/build.json: a fingerprint of the page's source and of the files built from it.
// tests/test_ask_page.py recomputes both, so a source edit without a rebuild, or a hand-edited
// bundle, fails the tests. Run by `npm run build`; never by Hub.
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "../../hub/static/ask");
const sha = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
const SOURCES = ["package.json", "package-lock.json", "vite.config.js", "record-build.mjs",
  ...readdirSync(join(here, "src")).sort().map((name) => "src/" + name)];
const BUILT = ["ask.css", "ask.js"];

writeFileSync(join(out, "build.json"), JSON.stringify({
  source: Object.fromEntries(SOURCES.map((name) => [name, sha(join(here, name))])),
  built: Object.fromEntries(BUILT.map((name) => [name, sha(join(out, name))])),
}, null, 2) + "\n");
