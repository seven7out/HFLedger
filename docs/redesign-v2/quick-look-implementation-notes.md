# Implementation notes — HFLedger Quick Look evidence preview (2026-07-18)

## Assumptions

- `ea00fc1801f3e426c92ccb4e02d8644edd75558f` is the accepted V1 integration
  commit because it is the clean integration branch tip, contains the complete
  dogfood report/screenshots, and all six Wave 2 merges precede it.
- Prompt 16 authorizes an isolated deferred branch for review, not integration
  into V1 or publication.
- A Quick Look–styled in-app overlay over orientation V2 JSON is preferable to
  native macOS Quick Look because the product is previewing bounded projected
  evidence text, not asking the OS to open files.
- Existing validated item source links remain the only permitted Open Source
  path; evidence references themselves are display-only.

## Decisions

- Use the existing served Today UI and DOM construction helpers. Add no server
  route, native command, Tauri capability, dependency, entitlement, filesystem
  read, or network fetch.
- Treat every projection field as untrusted display data and create preview
  content with text nodes only.
- Keep the full inspector unchanged as the deeper evidence view.
- Allowlist every closed orientation V2 evidence kind except
  `untrusted-excerpt` and `other`. Unknown kinds and empty claims use a fixed
  unavailable state and do not render raw claim text.
- Use a non-modal overlay so list focus, Up/Down selection, and Escape focus
  return behave like Mac Quick Look without an inert/modal DOM boundary.

## Deviations

- The packet names an “accepted V1 integration commit” but not its SHA. The
  repository territory resolves it to `ea00fc1`; the branch is pinned to the
  full SHA above.
- Prompt 16 had no dedicated queue id. Before coding, the work was admitted
  through the project task gate with a full spec.
- The first screenshot exposed insufficient contrast from a translucent card.
  The panel background was made opaque while retaining the bounded backdrop
  treatment; the updated wide and narrow screenshots were re-reviewed.
- The feature worktree's first isolated native compile exhausted the machine's
  remaining disk space. Only its generated partial Cargo target was cleaned;
  native tests and the app build then reused the accepted integration checkout's
  compatible Cargo target cache. The fixed-path signing scripts were pointed at
  that generated cache through the ignored branch-local `target` path.
- Native app interaction was not launched against the user's existing app data,
  because its active workspace could be private. The built bundle was signed and
  release-verified, while interaction dogfood used only the fictional Ovenlight
  live fixture in the served app UI.

## Additional owner asks filed

- None. Prompt 16 directly selects the work; no further architecture-changing
  or protected decision is required for this isolated, non-published branch.
