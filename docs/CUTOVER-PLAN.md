# Hub cutover planning — not operational approval

This development checkout is not the running service. No cutover is authorized by
this document. Private bindings, data and operational evidence stay outside Git.

## Isolated candidate probe

`hub.probe:app` is a separate ASGI application. Its only application route is
`GET /readyz`, returning a fixed `probe-only` response with
`production_ready: false`. It does not import the dashboard, configuration,
storage, connectors or workers. It has no dashboard routes or startup jobs.

An installed-package synthetic check should exercise this application in-process
without starting a listener. If a listener is separately approved, bind it only
to `127.0.0.1` on a reviewed unused port, with no proxy exposure, production
credentials or data. Do not change the existing service to point at the probe.

This proves only that the package's minimal ASGI application can respond. It does
not validate dashboard behavior, authentication, settings, data compatibility,
external integrations or production readiness. It is not a security sandbox.

The default `hub.main:app` behavior is preserved: startup warms the Gmail cache,
resumes interrupted organizer work and starts delayed archive checks. `/status`
contacts Gmail through the bridge and checks Keychain. Neither is a passive smoke
check. The probe does not make ordinary dashboard startup safe.

## Controlled dashboard startup (source implementation only)

`HUB_BACKGROUND_JOBS=disabled` suppresses all three startup actions. The default is
`enabled` for compatibility; any other explicit spelling fails configuration
loading rather than silently enabling work. This setting is read at process
startup, not changed through HTTP. No running service setting was altered.

The dashboard now also has authenticated `GET /readyz`: it reports process mode
and whether background startup jobs are enabled, with `production_ready: false`.
It reads no database, connector or Keychain, including with `?refresh=1`, and keeps
normal access/security/cache gates. The standalone `hub.probe` remains independent.

Disabling startup jobs is **not** a read-only/offline application mode. Dashboard
routes can still read live sources, consult Keychain, change private preferences,
start a worker or apply organizer actions under their existing permissions. Use
only synthetic storage, mocked services and enforced effect guards during local
candidate tests. Do not expose a candidate, reuse real credentials or point it
at live data merely because its startup jobs are disabled or `/readyz` succeeds.

## Preserve Preschool access during product separation

The preserved deployment source still registers a Preschool tool; this focused
Hub source intentionally does not. Replacing the combined dashboard without a
reviewed independently working Preschool service/link or route transition could
remove existing class access. Coordinate that dependency before Hub cutover.
Do not copy private Preschool implementation back into Hub as a shortcut.

`HUB_PRESCHOOL_URL` now provides that navigation boundary in source. It defaults
to absent: no unconfigured destination is advertised. A supplied value must be a
plain HTTPS service URL with a DNS host and root or `/preschool` path, without
credentials, query/fragment, control characters or an arbitrary route. Malformed
configuration fails rather than producing a misleading link. The home card and
navigation suppress referrers. Hub never fetches, probes, proxies, authenticates
to or imports Preschool, and does not mount `/preschool`. The setting itself is
not proof the destination is private, working, authorized or ready for adoption.

## Gates before a real candidate or cutover

1. **Artifact:** record exact commit, wheel digest, Python and dependency versions.
2. **Bindings:** define service identity, interpreter, command, startup mechanism,
   logging and explicit private `HUB_DATA_DIR`. Do not use package-relative data.
   Record approved endpoint/token-file references and login allowlist privately;
   do not copy credentials or grant access as a troubleshooting shortcut.
3. **Ingress:** verify loopback listening and the trusted Tailscale proxy boundary.
   Missing identity headers are treated as local access by the dashboard; prevent
   untrusted clients from reaching that path or supplying trusted headers.
4. **Background effects:** define how a real candidate is prevented from warming
   live caches, resuming work or running recurring rules. The probe is not a
   dashboard candidate. Never run two workers against the same production state.
5. **Data and journals:** inventory plans, recurring rules, AI opt-outs and bridge
   journal relationships. Reconcile uncertain effects before worker resumption.
   Database opening can create tables or add columns; do not open production data
   with a candidate merely to inspect it.
6. **Backup and restore:** define a consistent snapshot and any approved quiet
   period. Demonstrate restore into fresh private scratch without starting jobs.
   Check schema compatibility with the rollback version. Restoring an old database
   must not replay completed actions or revive disabled rules or withdrawn choices.
7. **Acceptance:** specify synthetic dashboard checks separately from explicitly
   authorized live checks. Preserve account roles, write restrictions, approved
   privacy routing, budgets, opt-outs and paused states. No sign-in, model request
   or connector effect follows from a probe succeeding.
8. **Decision:** obtain approval tied to the exact artifact, target, before-state,
   backup evidence, permitted interruption, checks and recovery procedure. Define
   failure thresholds and decision owner. Stop on uncertain outcomes; do not retry
   writes blindly or treat a code rollback as an undo of external effects.

## Evidence status

The prior installed-wheel results are in BUILD-VERIFICATION.md. They exclude live
bindings, startup-service configuration, backup restore and deployment acceptance.
The gates above remain open until specific evidence and owner decisions exist.

## Current owner direction: one private Hub

The owner chose one process and the existing private Hub address. Separate-service
steps in this historical document are superseded by PRIVATE-ADOPTION.md. Preschool
is packaged in Hub and uses its existing data and AI opt-out store.
