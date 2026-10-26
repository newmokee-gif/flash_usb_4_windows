# Publication recovery for v0.3.0

The recovery workflow publishes only the complete set of eight applications.
It builds Apple Silicon natively using the free `macos-latest` runner and reuses
the seven checked applications from run `40376220121`, after validating all
16 required successful jobs, artifact identities and archive SHA-256 digests.
Every application uses the immutable source commit
`6809f2cc4c816ff46f01078b0e784f8225ff0813` and tag `v0.3.0`.

GitHub failed to allocate ARM macOS runners during the original release attempt.
The user chose to wait for all eight platforms, not publish a partial release.
Automatic recovery attempts run hourly at minute 23 UTC until
**2026-10-28 00:00 UTC**, subject to GitHub scheduling and workflow concurrency.
An attempt already running is not interrupted by this deadline.

The control job disables only `release-recovery.yml` once all eight applications
are public, or when its deadline is reached and manual attention is needed.
Existing drafts are preserved and pause new builds; the workflow never deletes
or overwrites them. Changed tags or incomplete existing publications require
manual inspection. No paid runner, skipped native test or older ARM application
is substituted for the required build.

Before publishing, recovery rechecks all inputs and prevents the original
publisher from racing it. The original publication script uploads a draft,
downloads all uploaded files again and verifies their checksums, then publishes
the complete release and retains v0.2.1 as a recoverable draft.
