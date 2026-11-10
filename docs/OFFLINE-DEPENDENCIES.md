# Reviewed offline macOS dependencies

The release artifact includes `macos-arm64-wheelhouse.tar` for CPython 3.12 on an
ARM64 Mac, targeting macOS 11 or newer. It contains the exact 32 dependencies in
`requirements.lock`, including test dependencies and pip, plus a manifest and
hash-locked requirements. No installed environment is copied into this archive.

`tools/macos-arm64-wheelhouse.json` is the source-reviewed allowlist: exact source
lock digest, target, filenames, versions and SHA-256 for every wheel. Native wheels
are ARM64, compatible universal2 or stable-ABI wheels; pure Python wheels remain
platform-neutral. The producer verifies wheel distribution metadata against the
lock. It downloads from the standard HTTPS PyPI index, never an additional index,
with no source distributions, package installation or lifecycle scripts.

Normal hosted CI requires this committed manifest, uses pip's `--require-hashes`,
and rejects missing, extra or changed wheels. The sorted tar metadata is fixed,
so the same wheel inputs produce identical archive bytes. The tar is additionally
bound into the exact-SHA release receipt and authenticated GitHub artifact digest.
The Mini rechecks the source-committed manifest and every byte, then installs from
that wheelhouse with `--no-index --require-hashes --no-deps --force-reinstall` into
a fresh virtual environment. It never falls back to an online registry or existing
service environment. Full guarded installed-package tests run before selection.
This establishes transport and reproducible dependency bytes, not a new package
signing identity or a claim that third-party packages are vulnerability-free.

## Updating dependencies

Change the version lock deliberately. The separate manual
`Propose macOS dependency wheelhouse` workflow produces a proposal from that exact
source commit, not a deployable release. Review the successful workflow/source
identity, GitHub artifact digest, exact package/version set, platform filenames
and wheel metadata/hashes before committing the replacement manifest. Never
silently regenerate the reviewed manifest during a normal release build. If the
new package lacks a compatible wheel, stop rather than enabling source builds,
relaxing platform constraints or installing an unreviewed substitute.

The first manifest came from successful proposal run
https://github.com/phinneywood/hub/actions/runs/42385844582 at source
`5a92e3e184e29cc2a275e61a8df74cc6a5c3c1a3`. All 32 exact pinned distributions were
reviewed and match `requirements.lock`. Its deterministic wheelhouse SHA-256 is
`2dfdd9f6b005d624a0fe31f62e6a0db5322c8ba8bf0d90f947024efa8bb42c82`.

Local producer checks use fictional wheels, without a network or installation:

```sh
python3 tools/test_macos_wheelhouse.py
```

Source verification additionally requires the reviewed archive via
`tools/verify_build.py --macos-wheelhouse /absolute/path/to/macos-arm64-wheelhouse.tar`.
This preparation neither starts Hub nor changes live state or private access.
