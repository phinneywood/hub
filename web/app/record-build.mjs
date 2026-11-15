// Writes hub/static/app/build.json: a fingerprint of the app's source and of the files built from it.
// tests/test_app_page.py recomputes both, so a source edit without a rebuild, or a hand-edited
// bundle, fails the tests. Run by `npm run build`; never by Hub.
//
// With --clean it instead removes the three files the build owns, before a build. The folder is
// never emptied: the fonts and anything else placed in hub/static/app by hand stay.
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "../../hub/static/app");
const sha = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
// Every file under src, at any depth, as src/folder/name. Dot files (Finder's, an editor's) are not source.
const under = (folder) => readdirSync(join(here, folder), { withFileTypes: true })
  .filter((entry) => !entry.name.startsWith("."))
  .flatMap((entry) => entry.isDirectory() ? under(folder + "/" + entry.name) : [folder + "/" + entry.name]);
const SOURCES = ["package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "record-build.mjs",
  ...under("src").sort()];
const BUILT = ["app.css", "app.js"];

if (process.argv.includes("--clean")) {
  for (const name of [...BUILT, "build.json"]) rmSync(join(out, name), { force: true });
} else {
  writeFileSync(join(out, "build.json"), JSON.stringify({
    source: Object.fromEntries(SOURCES.map((name) => [name, sha(join(here, name))])),
    built: Object.fromEntries(BUILT.map((name) => [name, sha(join(out, name))])),
  }, null, 2) + "\n");
}
