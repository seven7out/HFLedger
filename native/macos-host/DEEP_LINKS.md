# Native item-link boundary

The installed macOS bundle registers one custom scheme through Tauri's official
deep-link plugin:

```text
hfledger://item/<workspace-id>/<item-id>
```

This is a navigation capability only. The native host accepts one URL per OS
activation, checks the exact lowercase scheme and `item` authority, rejects
queries, fragments, user information, ports, extra path segments, noncanonical
percent escapes, and overlong identifiers, then requires an exact workspace id
already present in the app's private settings. Item ids use the normalized
`item-` plus 24 lowercase hexadecimal characters shape.

An accepted link may show an already-running board, recreate a closed board
window, or start the local engine for the allowlisted workspace. It then places
only the normalized item id in the loopback page fragment. Fragments do not
enter HTTP requests or engine logs. The page consumes that fragment to open the
existing inspector and must clear it afterward. An unknown or deleted item is
reported as unavailable; it does not fall back to a source path, URL, command,
or similarly named item.

Workspace labels and paths are never accepted from or copied into a link. The
native parser does not mutate authoritative workspace files or local triage
state, resolve or answer anything, snooze, open sources, or expose a JavaScript
deep-link IPC permission. The single-instance plugin remains first and forwards
subsequent installed-app activations to the same Rust-only handler. Validated
callbacks enqueue timestamped intents; one background worker serializes the
full workspace start/switch and final navigation, and suppresses only an
immediate duplicate cold-start callback.

Scheme registration is a bundle property. A development binary does not prove
the macOS launch path. The isolated installed-candidate check, including
`Info.plist`, signature, cold/running/no-active lifecycle, malformed,
unregistered, stale, and cross-workspace cases, is recorded in
`docs/redesign-v2/search-links.md`.
