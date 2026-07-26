# Today primary surface

## Outcome

HFLedger for Mac now treats the selected workspace's Today window as its
primary surface. Cold launch, single-instance activation, the status item, and
macOS Dock reopen all use one restore routine. Closing Today hides that window;
it does not reveal the former workspace launcher.

The former launcher window is now a hidden-by-default Settings surface. It
retains workspace management, preferences, engine recovery, backups, and
diagnostics. A new installation has no automatically selected workspace and
shows bounded onboarding. Invalid or missing selected workspaces show bounded
recovery. The included fictional workspace is registered and opened only after
an explicit user action.

## Boundary

This change does not alter the board, ledger, observer, or local-triage schemas.
The selected workspace still starts the frozen engine with the same stable
workspace registration and loopback-only transport. The no-write-back and
closed native-command boundaries are unchanged.

## Verification

- 20 native Rust tests, including the primary-surface lifecycle matrix
- `cargo clippy --locked --all-targets -- -D warnings`
- 242 Python and Node tests through `scripts/release-check --allow-dirty`
- ad-hoc signed `.app` build and bundle privacy/integrity verification
- isolated-identifier first-run launch confirmed the bounded onboarding state;
  no private observer path or installed-app profile was used

No app was installed, published, notarized, or connected to an updater.
