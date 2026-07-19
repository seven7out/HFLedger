# Global search and safe item links

## Status and boundary

Prompt 20 adds an isolated, local-only search and navigation capability on
`deferred/redesign-v2-search-links`. Search reads already-projected Orientation
V2 metadata. Item links select a registered native workspace and open the
existing read-only inspector. Neither surface grants task, decision, evidence,
source-opening, filesystem, or command authority.

Prompt 22 must review this branch before integrating it with other deferred
capabilities. No remote service, embedding, model call, collector activation,
publication, or updater change is part of this work.

## Search contract

`core.search_links.search_projected_metadata()` accepts at most 32 closed
workspace/context records containing opaque public ids and a validated
Orientation V2 projection. It performs no discovery, filesystem read, network
request, clock read, persistence, or mutation. Private workspace display names
may be supplied by a native caller but are neither indexed nor returned.

The bounded searchable surface is:

- item title and short `whyHere` reason;
- stable normalized item id and a path-free stable source reference;
- project, status, primary home, provenance, and entity kind;
- kinds, labels, statuses, provenance, and path-free references from exact
  same-item evidence/run associations.

The search never indexes Copy Context, evidence claims, change summaries, raw
or unknown fields, diagnostics, collector errors, link targets, filesystem
paths, URLs, private workspace labels, logs, or remote pages. Results contain
only the opaque workspace/item identities needed for inspector navigation and
bounded row context: title, All Work destination, primary home, project,
status, provenance, and the public rank band.

Queries are canonicalized with NFKC and locale-independent case folding,
limited to 128 characters and 12 tokens. Combined projections are capped at
10,000 items plus bounded run/change/evidence maps. Results are capped at 50,
with exact total/truncation disclosure.

## Explicit ranking

There is no numeric relevance score, semantic similarity, fuzzy title match,
or hidden tie-breaker. The first applicable band wins:

1. `exact-id`: exact normalized item id or exact admitted stable source id;
2. `exact-title-or-id-prefix`: exact normalized title or prefix of an admitted
   stable id;
3. `title-token`: every query token is a complete normalized title token;
4. `metadata`: bounded substring or complete-token match in the admitted
   metadata surface.

Within a band, rows sort by folded title, opaque workspace id, context id, and
normalized item id. Reordering workspaces or projection arrays does not change
output.

There is one production ranker. The served Command-K surface calls its
loopback `/api/search` route for the active context. The native Command-K menu
opens the launcher, which invokes the same bundled engine once across every
registered workspace and context. Results include opaque workspace/context
identity, destination, status, provenance, and rank band. Selecting an exact
result switches the allowlisted workspace/context to All Work and opens the
existing inspector. Command-F remains the narrower current-view filter. Search
selection does not open an authoritative source.

Search terms do not enter engine request logs or native child-process
arguments: loopback request logging redacts the query string and the native
host sends the bounded UTF-8 query over stdin. Native searches are one-flight,
stderr is discarded, and stdout is killed at a live 256 KiB cap before the
closed response schema is parsed.

## Native item-link grammar

The installed macOS bundle statically registers exactly one custom scheme:

```text
hfledger://item/<workspace-id>/<item-id>
```

The Rust host accepts only the exact lowercase scheme and `item` authority,
two canonical path segments, an already-registered opaque workspace id, and an
item id matching `item-` plus 24 lowercase hexadecimal characters. It rejects
queries, fragments, user information, ports, backslashes, extra segments,
encoded separators, double encoding, noncanonical percent encoding, invalid
UTF-8, and overlong input.

Workspace labels and paths never appear in the link, public error, loopback
URL, or page event. Unknown/unregistered workspaces fail closed in the native
launcher. Unknown or deleted items produce one generic unavailable notice in
the board; there is no fuzzy fallback. Link rejection uses a separate
transient navigation notice and never changes a healthy engine's status to
crashed.

## App lifecycle

The official Tauri deep-link plugin owns static bundle registration. The
single-instance plugin remains first and forwards subsequent activations to
the same Rust handler. The host also consumes the plugin's cold-start URL.
Callbacks validate and enqueue bounded intents without waiting for engine
startup. One background worker serializes the entire workspace transition and
navigation. Producer timestamps suppress the duplicate cold-start callback
without suppressing an intentional later activation.

After allowlisting, the host either focuses the active board, recreates a
closed board window, or starts the selected registered workspace. It passes
only the normalized item id in `#item=<item-id>` on the loopback board URL. A
fragment survives cold page loading without entering HTTP requests or engine
logs. The served page validates the exact item-id shape, clears the fragment,
switches to All Work, and opens the existing inspector. No link can answer,
acknowledge, snooze, watch, complete, resolve, open a source, run a command, or
select an arbitrary path/URL.

The external board retains no general Tauri IPC or deep-link plugin
permission. Scheme parsing, private workspace allowlisting, process startup,
and workspace switching stay in Rust.

## Verification inventory

The focused suites cover:

- all four ranking bands, deterministic ties, Unicode normalization, empty and
  overlong queries, caps, duplicates, caller byte preservation, and multiple
  workspaces;
- exact association of run/evidence metadata and exclusion of raw claims,
  summaries, link targets, URLs, paths, unknown fields, and private labels;
- exact scheme/authority/path grammar, canonical encoding, workspace
  allowlisting, active/closed/switch lifecycle plans, and path-free handoff;
- served item-id/hash validation and navigation-only UI behavior.

## Installed-app dogfood — 2026-07-19

An ad-hoc-signed candidate built from this worktree was installed at
`/Applications/HFLedger Prompt 20.app` under the isolated identifier
`com.hfledger.desktop.prompt20`; it did not reuse the existing HFLedger app's
private state. The installed `Info.plist` contained exactly one URL scheme,
`hfledger`, and `codesign --verify --deep --strict` passed after installation.

The installed process passed these fictional-data checks:

- cold link with no engine running launched the installed bundle, started the
  allowlisted demo at `127.0.0.1`, selected All Work, and selected the exact
  normalized item in durable app-private navigation state;
- a running-app link kept the host and engine PIDs stable while selecting a
  second exact item;
- malformed and unregistered links left the running host, engine, selected
  item, and authoritative workspace bytes unchanged; with no active engine,
  the same malformed input did not start one;
- a valid but stale item id kept the engine healthy, preserved the prior
  selection, and produced no action or fallback navigation;
- two registered fictional workspace paths produced 14 scanned items and four
  deterministic `timer` results with distinct opaque workspace identities;
  an exact link switched the engine to the second canonical path, and another
  exact link switched back to `demo` with separate durable navigation state;
- the installed frozen engine accepted the query only over stdin, and the
  loopback engine log showed no raw search term or URL fragment.

The candidate and its isolated private state were removed after the run, and
the existing `/Applications/HFLedger.app` registration was restored. Visual
captures used the installed candidate's loopback engine in the in-app browser
because macOS screen-recording access was unavailable to this session:

- [`search-active-board.png`](screenshots/search-active-board.png) — bounded
  `timer` results with context, status, provenance, and rank band;
- [`deep-link-inspector.png`](screenshots/deep-link-inspector.png) — the exact
  All Work row and existing inspector reached without performing an action.
