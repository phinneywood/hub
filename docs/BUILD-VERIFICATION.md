# Installed-package verification

## Repeatable private CI (build only)

`.github/workflows/build.yml` verifies pushes and pull requests on a GitHub-hosted
Python 3.12 runner. Its token has read-only contents permission; checkout does not
persist credentials. There are no secrets, provider commands, deployment jobs,
service changes or release actions. Public Family Site hosting stays paused.

For a clean committed checkout, provision a separate Python 3.12 build environment
with `pip install -r tools/build-requirements.txt`, then run that interpreter with
`-I tools/verify_build.py`. The script exports exact HEAD with `git archive`, builds
an sdist and then its wheel, compares every package file byte, checks all archived
Python test fixtures and the lock, installs locked dependencies into fresh scratch,
runs `pip check`, and runs the archived tests against only that installed wheel.
For offline local checks, `--dependency-env /path/to/existing/venv` copies only
locked distribution files after verifying versions; it never changes that venv.
Normal CI uses binary dependencies installed from `requirements.lock`.

Tests receive an empty scratch HOME and no inherited service configuration. Plugin
autoload is disabled. Audit guards reject connections/DNS, listeners, subprocesses
and real-home file reads except the Python runtime and scratch environment. These
are accidental-effect guards, not a security sandbox for hostile code.

The scratch `verification.json` records exact commit, Python version, dependency
mode, package count and SHA-256 digests for source archive, sdist and wheel. CI
retains only this receipt for 14 days, never runtime data, content, logs, source
packages or credentials. The built packages are synthetic-verification source
artifacts, not a reviewed live-content bundle or an approved serving release.
No byte-reproducibility or live acceptance is claimed. Changes must be committed
before verification; candidate scratch snapshots are not remote CI acceptance.


## Independent-product navigation verified — 2026-11-07

- Added an optional validated Preschool HTTPS navigation link; absent by default,
  with referrer suppression and no backend fetch/proxy or private-code dependency.
- Fresh source archive -> wheel -> independent installation passed **304 tests**,
  using archive-supplied test fixtures and the established effect guards. All
  **69 package files** matched source and dependency checks passed.
- Tests cover absent links, malformed/credential-bearing/parameterized targets,
  normal navigation rendering and continued absence of a Hub `/preschool` backend.
- Wheel SHA-256: `b7a8d9712a4f26e81ebb213e9a8396529c4f5fd8fd2291cb77d81b38b194423c`.
  This identifies the synthetic verification artifact, not an approved deployment.
- No configured live URL, listener, proxy route, Keychain or source system was used.

## Controlled-startup verification — 2026-11-07

- Added strict startup-job suppression and a connector/storage/Keychain-free
  dashboard `/readyz` route. Default production behavior remains enabled.
- Fresh source archive -> wheel -> independently installed environment passed
  **277 tests**, using tests copied from the archive, locked dependencies, empty
  test home and the existing network/subprocess/home-read audit guards.
- All **69 package files** matched source bytes and `pip check` passed. Tests cover
  disabled lifecycle/readiness effects, access rejection, invalid setting refusal,
  and the enabled startup sequence using mocked jobs (never real background work).
- Wheel SHA-256: `2b89ed822ff506dac121d95e01112267273be5f7291d102902b7b0f731ebcdc8`.
- This does not establish a fully offline dashboard, production acceptance or live
  configuration. No listener, live data, source-system request or Keychain read was
  used. Existing framework deprecation warnings remain.

## Source-archive completeness verified 2026-11-07

- Added MANIFEST.in so the source distribution carries the locked environment,
  test fixtures (including conftest.py), operational plan and repository guidance.
  Previously the wheel was verified using separately copied checkout tests; the
  source archive did not carry all of those supporting files.
- Built a fresh source archive and its wheel; checked the archived lock, fixture,
  probe test and cutover-plan bytes against source. Copied tests **from the source
  archive**, then ran them against the installed wheel outside the checkout.
- **265 tests passed**, all **69 package files** matched, and `pip check` passed.
  The same isolated-home, no-network/subprocess audit guards and plugin-autoload
  restriction were used. No listener or live connector was exercised.
- This run's wheel SHA-256:
  `a383315375abe244254a6abc52df6453e64416f51bfb1ea55605fa89b3d9b6af`.
  Build outputs remain outside Git; the digest identifies this particular artifact,
  not a promise of byte-reproducible future builds or an approved production release.

## Probe addition verified 2026-11-07

- Built a fresh source archive and its wheel using the existing isolated builder;
  installed the wheel and locked dependencies into a fresh Python 3.12 environment.
- All **265 tests passed** against the installed wheel outside the checkout,
  including 14 isolated-probe tests. All **69 package files** matched source bytes;
  `pip check` passed. Test plugin autoload was disabled, with synthetic storage,
  an empty test home, and the prior network/subprocess/home-read audit guards.
- Probe tests cover lifecycle, application-import isolation, absence of dashboard
  and documentation routes, mutation rejection and explicit non-production status.
- Wheel SHA-256: `8e873fbd12cacc424a8c8e3a7c46860b4a7d64ec5eb45b420b136011cd9058dd`.
  Build artifacts remain outside Git. This wheel is not a production release.
- No listener, real connector, Keychain, production data or service was exercised.
  Existing startup-handler and Starlette/httpx warnings remain. The minimal probe
  does not verify the dashboard's production startup or make it safe.

## Verified 2026-11-06

- Python 3.12.14 on macOS ARM64; clean, separate build and test virtual environments.
- Build tools: build 1.6.1, setuptools 84.2.1, wheel 0.48.0,
  packaging 26.3, pyproject_hooks 1.3.3.
- Built `personal_hub-0.1.0.tar.gz`, then its wheel from that source archive.
- Installed all dependencies from `requirements.lock`, then the local wheel with
  `--no-index --no-deps`. `pip check` passed.
- All 68 package files, including nested templates and static assets, matched the
  source byte-for-byte inside the wheel.
- Copied tests into a separate working directory with no application source.
  Ran Python with `-I` and pytest with `--import-mode=importlib`.
- Verified `hub` imports from the test environment's site-packages; neither
  Preschool nor Family Site was installed. All **251 tests passed**.
- Used synthetic storage, an empty test home and absent credential paths.
  An additional Python audit hook rejected network/DNS, real subprocess execution
  and reads of the real home except the Python runtime. Pytest plugin autoload
  was disabled. These are accidental-effect guards, not a security sandbox claim.

## Repeat the verification

1. Export a committed source snapshot with `git archive HEAD` to fresh scratch.
2. Use Python 3.12 to create a build virtual environment; explicitly install the
   build-tool versions above from PyPI. Run `python -I -m build --no-isolation`
   against the exported source, keeping artifacts outside the checkout.
3. Create another virtual environment without system site-packages. Install the
   exported `requirements.lock` with binary-only dependencies, then install the
   local wheel with `pip install --no-index --no-deps`. Run `pip check`.
4. Copy only `tests/` to a new working directory. Run the installed interpreter
   with `-I -m pytest --import-mode=importlib tests`; retain the synthetic fixtures,
   empty test home, absent credential paths and additional effect guards above.
5. Confirm imported module paths point to that environment, compare wheel contents
   against the exported package files, and record artifact SHA-256 hashes locally.

## Not verified

No live connectors, mail, models, Keychain access, production data, startup-service
configuration, backup restore or deployment was tested. Runtime environments were
not modified. FastAPI startup-handler and Starlette/httpx deprecation warnings
remain; dependency upgrades and lifecycle changes are separate work.
