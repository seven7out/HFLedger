# HFLedger for Mac

This directory packages the existing HFLedger engine and canonical HTML board
as a native Tauri 2 macOS application. The app remains local-first: it manages
explicit workspaces, starts a loopback-only engine, and opens the board in a
separate native window.

## What is native

- workspace creation and a native picker for existing workspaces;
- a pinned, frozen Python 3.9 engine with no system-Python dependency;
- single-instance lifecycle, dynamic loopback ports, crash status, and restart;
- private rotating logs, validated manual backups, and diagnostics;
- a searchable in-app guide to optional runtime-adapter workflow shortcuts,
  including the difference between read-only, planning, autonomous, and
  attended-build commands;
- saved window state, a menu-bar item, Dock attention badges, notifications,
  and optional Launch at Login;
- native View/Item commands for Today, Changes, All Work, Shipped Log, Watched,
  filtering, inspector/sidebar visibility, open, acknowledge, snooze, watch,
  and Copy Context;
- private seen, snooze, acknowledge, watch, navigation, pane, and disclosure
  state keyed by stable workspace registration across dynamic port changes;
- debounced refresh of only the configured board, ledger, collector, and
  adapter files, without recursive discovery or a primary Refresh button;
- `.app` and DMG builds with signature, bundle-integrity, and privacy checks.

The loopback board has no general Tauri IPC authority. Native commands enter
the page only as an allowlisted one-way event; page state returns through the
closed loopback API. The native settings window owns filesystem selection and
process control, while the served HTML remains the single implementation of
Today and Decision Deck behavior.

## Keyboard and menu behavior

The normal HFLedger, File, Edit, View, Item, Window, and Help menus expose the
same commands as the served interface. Command-1 through Command-5 open Today,
Changes, All Work, Shipped Log, and Watched. Up/Down select rows; Left/Right
collapse or traverse groups; Space toggles the bounded evidence preview;
Return or `O` opens the one supported source; `E` acknowledges locally; `S`
snoozes locally; `W` watches; Command-F filters the current destination;
Command-K opens the command reference; Escape closes the top transient surface
and restores focus.

These shortcuts do not fire from editable controls. Today has no Answer,
Resolve, Complete, Skip, merge, deploy, or arbitrary source command. Owner
outcomes remain in the Decision Deck or named authoritative application.

## Build locally

Requirements: macOS 14+, Xcode command-line tools, Rust/Cargo, Node.js, and
Python 3.9+. Python is a build dependency only.

```sh
cd native/macos-host
npm ci
npm run build
```

The first build creates ignored `.build-venv/`, `.engine-build/`, and
`src-tauri/runtime/` directories. PyInstaller and all of its build dependencies
are exact-version pinned in `requirements-build.txt`. Runtime license notices
are included beside the frozen engine.

The app bundle is written to:

```text
src-tauri/target/release/bundle/macos/HFLedger.app
```

`npm run build:dmg` also produces a drag-to-install DMG. Local builds use an
ad-hoc signature and are suitable for dogfooding, not public distribution.
`npm run verify` rechecks the app signature, frozen engine, symlink containment,
machine-path privacy denylist, and artifact hashes.

## Runtime data

App settings and managed data live under:

```text
~/Library/Application Support/com.hfledger.desktop/
```

The app registers only folders the user creates or explicitly chooses. It does
not silently open conventional project or ledger locations.
Settings are closed-schema, atomically replaced, and mode `0600`; app-owned
directories are mode `0700`.

The included Ovenlight workspace is fictional and persists across app upgrades.
Removing a workspace from future app settings must never delete its data.

Private UI state lives under the app data directory in a separate mode-`0700`
tree with mode-`0600` files. It is excluded from workspace backups and public
artifacts. Browser-only use has process-session state instead; it does not
provide native menus, file watching, Dock badges, window restoration, or
durability after the server process exits.

## Public release gate

The manual `Mac app candidate` GitHub workflow always builds an ad-hoc candidate.
Its optional signed-release job is off by default and creates only a **draft**
release. That job requires an attended `macos-release` environment plus these
repository secrets:

- `APPLE_CERTIFICATE` and `APPLE_CERTIFICATE_PASSWORD`;
- `APPLE_SIGNING_IDENTITY` (a Developer ID Application identity);
- `APPLE_ID`, `APPLE_PASSWORD` (an app-specific password), and `APPLE_TEAM_ID`;
- `KEYCHAIN_PASSWORD` for the temporary CI keychain.

The updater is intentionally disabled until a stable release URL and a Tauri
updater signing public key are approved. Developer ID signing, notarization,
publishing a draft, and enabling update delivery are separate attended gates.

This branch provides a Quick Look–styled in-app evidence preview without using
the macOS Quick Look framework or adding native authority. Transition-based
attention notifications, a rich menu-bar status/popover, analytics, advanced
disputes, multi-machine skew detection, global search, and custom deep links
remain deferred. The current app must not present empty controls or
documentation that implies those capabilities are shipped.
