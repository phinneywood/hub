# Current state

## Live through the Hub deployer — 2026-11-10

- Hub runs as one private service from the deployer's `releases/current`. Each version is built
  from a pushed `main`, tested as an installed package, staged, then deployed by the owner's tap.
- Deployments since 2026-11-09 include the engine catalog, Gmail review, calendar source, model key
  from 1Password and the Kitchen tab. The latest accepted release is the Kitchen chat-style layout.
- Every deploy checks health, page loads, refusal of strangers and cross-site writes, backs up state
  and verifies each database; any failure rolls back automatically.
- Sections below are earlier development checkpoints, kept for history.

## Independent Preschool navigation — 2026-11-07

- Optional `HUB_PRESCHOOL_URL` now renders a plain validated/referrer-suppressed
  link in navigation and the home page. It defaults to absent and never proxies,
  fetches or imports private Preschool code/data. No live URL was configured.
- **304 installed-wheel tests passed**, 69 package files matched and dependency
  checks passed with source-archive fixtures and the existing synthetic effect
  guards. Independent Preschool access must still be accepted before Hub cutover.

## Controlled dashboard startup — 2026-11-07

- `HUB_BACKGROUND_JOBS=disabled` now suppresses cache warming, worker resumption
  and delayed archive startup. Default behavior is preserved; invalid values fail
  closed. Dashboard `/readyz` checks process readiness without connector, Keychain
  or storage reads and explicitly does not claim production readiness.
- **277 installed-wheel tests passed**, 69 package files matched and dependencies
  checked. Tests use source-archive fixtures and synthetic/effect-guarded data.
- This is not an offline/read-only dashboard mode: interactive routes retain their
  existing effects. No production configuration, service, private data or job changed.
- Hub cutover must also preserve access to the separately extracted Preschool
  product. See docs/CUTOVER-PLAN.md for this coordination dependency and limits.

- Development source extracted; no production cutover or restart.
- Runtime data and credentials were not copied.
- Original deployment checkouts and their dirty working trees are preserved.
- Private source remote: `phinneywood/hub`; no production publication or new schedule.
- See HANDOFF.md for the next narrowly scoped task.

## Candidate planning — 2026-11-07

- Source archives now include locked dependencies, test fixtures and operational
  guidance. Fresh tests copied from the archive pass against its installed wheel:
  265 tests, 69 package files verified; see docs/BUILD-VERIFICATION.md.

- Added isolated `hub.probe:app`, serving only a fixed `/readyz` response with
  `production_ready: false`; ordinary dashboard startup is unchanged.
- Installed-wheel verification now passes 265 synthetic tests with all 69 package
  files checked. See docs/BUILD-VERIFICATION.md.
- docs/CUTOVER-PLAN.md records startup effects, private bindings, data/journal,
  ingress, backup/restore, acceptance and exact approval gates. All operational
  gates remain open; no service, credential, production data or schedule changed.

## Source verification

- 251 Python tests passed with synthetic fixtures.
- No live source/device test, model call, or deployment acceptance claimed.
- Source archive and wheel built in a separate Python 3.12 environment.
- All 251 tests passed against the installed wheel outside the checkout;
  dependency checks and packaged-file completeness checks passed.
- See docs/BUILD-VERIFICATION.md. Production bindings and cutover remain unverified.
