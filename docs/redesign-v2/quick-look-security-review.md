# Quick Look–style evidence preview — security review

Date: July 18, 2026

Branch: `deferred/redesign-v2-quick-look`

Base: accepted V1 integration commit
`ea00fc1801f3e426c92ccb4e02d8644edd75558f`

## Decision

HFLedger uses a Quick Look–styled in-app panel over the existing orientation V2
projection. It does not use the macOS Quick Look framework.

The product is previewing bounded projected text, timestamps, provenance, and
safe link metadata—not file contents. Native Quick Look would therefore add a
content-loading boundary, native wiring, and possible entitlement/capability
questions without adding value. The in-app panel preserves the served Today
interface as the single implementation and requires no native, server, engine,
or protocol change.

The preview is non-modal so the selected ledger row retains keyboard focus.
Space toggles the panel, Up/Down changes the selected item and updates the open
preview, and Escape closes the panel and returns focus to the current row. The
full inspector remains the deeper dossier.

## Data flow and authority

```text
validated orientation V2 JSON
        |
        v
closed item/evidence projection lookup + server-owned link resolution
        |
        v
bounded plain-text preview model
        |
        v
DOM nodes created with textContent
```

The preview performs no content fetch, filesystem read, file discovery, HTML
parsing, shell execution, native invoke, collector action, local-state write,
or authoritative mutation. The existing page load fetches the read-only
`/api/links` resolver response once per projection. Optional Open Source buttons
join that response to projected labels by exact link id, then reuse
`safeLinkTarget` as defense in depth and `openSafeTarget` for attended
navigation. Raw projected targets and evidence references are display-only and
can never become navigation targets by themselves.

## Evidence allowlist

Quick Look renders a claim only for these already-projected kinds:

- `status`
- `progress`
- `blocker`
- `review`
- `test`
- `ci`
- `pull-request`
- `merge`
- `deployment`
- `completion`
- `owner-report`
- `collector-health`
- `local-artifact`

`local-artifact` means projected artifact metadata only. Quick Look never
opens or reads the referenced artifact.

The kinds `untrusted-excerpt` and `other` are deliberately not previewable.
Unknown kinds are also rejected. Their raw claim is not rendered; the panel
shows a fixed reason and offers Open Source only when the projection supplies a
separately validated link. A missing evidence id, empty claim, unsupported
link, or malformed record receives an explicit unavailable state.

## Bounds and presentation

- At most eight of the item's at-most-50 evidence records appear.
- Item title is capped at 180 characters; reason at 280; claim at 500; source
  label at 180; and source reference at 800.
- At most three named observation gaps appear.
- Markup remains literal text because every value passes through `safeText`
  and every rendered element uses `textContent`/created text nodes.
- Provenance is normalized to the five canonical words. Unknown provenance
  fails closed to `unobserved`; the preview never upgrades a claim.
- `verified`, `agent-reported`, `inferred`, `unobserved`, and `disputed` stay
  visible as words and do not rely on color.

## Threat review

| Threat | Result |
| --- | --- |
| Arbitrary file read or missing-file probe | Impossible in this feature. File-shaped references are literal text; there is no file API or native invoke. |
| Symlink or `../` path escape | No path is resolved. Escape-shaped references remain bounded display text. `file:` links are rejected by the existing link validator. |
| Remote fetch or untrusted HTML render | The preview makes no content request and never assigns HTML. Resolver-approved `http`/`https` source links navigate only after a user activates Open Source. |
| Script/markup injection | Test corpus preserves tags as visible text and proves no `innerHTML`, `outerHTML`, or `insertAdjacentHTML` path exists. |
| Huge evidence or UI denial of service | Projection and preview caps are applied before DOM construction; the panel scrolls within a bounded viewport. |
| False corroboration | Provenance is copied from the projection's closed vocabulary; unsupported values become `unobserved`. |
| Accidental Today write-back | No route or command was added. The only Today POST remains `/api/local-state/command`; Quick Look does not call it. |
| Focus loss or hidden keyboard trap | The panel is non-modal, selection retains focus, arrow navigation updates the preview, and Escape restores the current selected row. |
| Private-data artifact leak | Tests and screenshots use only the fictional Ovenlight fixture. No private observer is opened for this deferred branch. |

## Verification

Focused automated checks:

- `node --check app/static/app.js`
- `node --test tests/ui/test_app.js`
- `python3 -m unittest tests.ui.test_today_ui`
- `git diff --check`

The focused suite covers no selection, explicit allowlist behavior, invalid
provenance, malformed/missing evidence, huge claims, unsupported kinds,
untrusted markup, missing/path-escape-shaped references, unsafe `file:` links,
missing/unresolved resolver records, refusal to trust a raw projected target,
keyboard ordering, selection updates, focus return, and the light/dark/reduced
motion CSS contracts.

Fictional live-browser dogfood passed in forced light and dark appearance at
1,440, 1,200, 720, and 600 CSS pixels:

- Space opened the selected item's preview.
- ArrowDown changed the selected row from the verified decision to the disputed
  shipment and updated the preview title/provenance without closing it.
- Escape closed the preview and focus remained on the disputed selected row.
- The 720- and 600-pixel layouts had no horizontal overflow; at 600 pixels the
  summary and footer stacked.
- The source, two clocks, dispute label, and read-only handoff remained visible.

Screenshots (fictional data only):

- [Wide light appearance](screenshots/quick-look-wide-light.png)
- [Narrow light appearance](screenshots/quick-look-narrow-light.png)
- [Wide dark appearance](screenshots/quick-look-wide-dark.png)

Full verification passed:

- `python3 tests/run_all.py`: 205 repository tests
- `./scripts/release-check --allow-dirty`: release ready for HFLedger 0.4.1
- `cargo test --locked`: 12 native-host tests
- `cargo clippy --locked --all-targets -- -D warnings`: zero warnings
- Tauri release build plus the repository signing wrapper: ad-hoc signed
  HFLedger 0.6.0-alpha.4 app bundle containing the frozen HFLedger 0.4.1 engine
- `scripts/verify_release.py` and `codesign --verify --deep --strict`:
  signature, frozen engine, symlink containment, machine-path privacy denylist,
  and artifact hashes verified
- SHA-256 comparison: the bundled `app/static/app.js` is byte-identical to this
  worktree's Quick Look implementation

The app bundle was built from this worktree with the accepted integration
checkout's Cargo target cache because the feature worktree's first isolated
compile exhausted the machine's remaining disk space. Cargo still compiled the
feature worktree source, and the branch-local generated `target` path was linked
to that cache for the repository's fixed-path signing and verification scripts.
The `npm run build` wrapper therefore reported its fixed-path lookup failure
after Tauri had successfully built and signed the bundle; the signing and
verification scripts were rerun explicitly through the linked generated path.
No installed app, user settings, or authoritative workspace was changed. Live
interaction dogfood remained on the fictional Ovenlight fixture so this
deferred review branch never opened a private observer.
