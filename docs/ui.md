# Local reference interface

HFLedger is a quiet ledger browser for work spread across agent runtimes. Its
primary check-in loop is:

> What changed since I last looked, and what needs me now?

The loopback service serves a Mac-oriented Today browser and the existing
phone-sized Decision Deck from the same validated board and append-only ledger.
There is no UI task database. Today is a read-only orientation projection; the
Decision Deck remains the owner outcome surface.

## Start the service

```sh
export LEDGER_HOME=/path/to/private-ledger-data
./cli/ledger serve
```

The configured port defaults to 7171 and can be overridden for one run with
`ledger serve --port PORT`. Today is at `/`; the Decision Deck is at `/deck`.

The service binds to `127.0.0.1`, accepts only loopback Host headers, does not
enable CORS, and has no remote-authentication layer. Do not put an
unauthenticated network proxy in front of it.

## What Today shows

The wide interface uses a source sidebar, a center ledger, and an evidence
inspector. At narrower widths the inspector becomes a drawer and then replaces
the center pane with a Back action. The interface stops shrinking below 600
points instead of compressing evidence into unreadable columns.

The sidebar order is fixed:

1. Today
2. Changes
3. All Work
4. Shipped Log
5. Watched
6. Projects
7. coverage footer

Today is ordered by consequence, not by a weighted score:

1. one coverage meta-alert when a global observation gap invalidates the view;
2. at most seven ranked attention rows;
3. changes since the last successful visit, grouped by exact run id;
4. at most three quiet-while-observed concerns; and
5. a link to parked or unobserved work in All Work.

Every item has exactly one primary home, in this precedence order:

`needs-you`, `disputed`, `silent-while-observed`, `shipped-unverified`,
`in-motion`, `queued`, `shipped-verified`, `parked`, `unobserved`.

Changes is a journal, not a second home. A run may refer to an item already in
All Work without duplicating it as another status row. Watched is also a local
filter, never a primary home. Shipped Log defaults to independently verified
shipments.

## Evidence, provenance, and two clocks

The inspector explains why an item is present, one supported next action,
bounded Copy Context, evidence, named missing observations, history, safe source
links, and freshness. Every claim uses one exact provenance word:

| Word | Meaning |
| --- | --- |
| `verified` | A named authoritative record or fresh independent typed observation establishes the exact claim. |
| `agent-reported` | An agent/runtime reported the claim, without independent outcome proof. |
| `inferred` | A fixed documented rule derived the claim from typed fields. |
| `unobserved` | A required source or exact association is missing or unusable. |
| `disputed` | Exact non-superseded evidence makes incompatible claims about the same item and claim kind. |

A passing test verifies that test, not a deployment. A board `Done` value
verifies board status, not shipment. An agent-reported shipment becomes
verified only after an independent exact observation such as a merged pull
request on the configured branch, a successful typed deployment, or a
validated named artifact.

Two clocks are always separate:

- **Item changed** is when the underlying item or claim changed.
- **Sources observed** is when every required source was most recently read
  successfully; the displayed time is the oldest success among them.

The time the projection was generated, the newest observed change, and the
last attempted collection never substitute for either clock.

## Quiet requires observation

`silent-while-observed` means activity was expected, the silence window passed,
and every required source/scope successfully covered that window. It is not a
synonym for old, parked, disabled, or missing.

Source health uses seven states:

| State | What it means | Can prove quiet? |
| --- | --- | --- |
| `disabled` | Explicitly off or not consented to | No |
| `never-observed` | Enabled but no completed attempt exists | No |
| `unavailable` | The latest attempt produced no usable scope data | No |
| `degraded` | The latest attempt was partial, truncated, or impaired | Only for an explicitly unaffected healthy/idle scope |
| `stale` | A prior success exists but its freshness window passed | No |
| `idle` | A complete current observation returned no records | Yes |
| `healthy` | A complete current observation returned records | Yes |

Disabled, stale, unavailable, degraded, or never-observed evidence makes the
affected claim unobserved. It never produces “nothing changed,” “all clear,” or
quiet. Empty success is always qualified, for example: “Nothing needs your
attention in the sources observed through 5:55 PM.”

## Local presentation state

Today may change only app-private presentation state:

- mark exact changes seen after a successful visible render;
- acknowledge or snooze the current attention generation;
- watch or unwatch an item;
- remember the selected destination/item, pane widths, and disclosure state.

These actions never edit `board.json`, append `ledger.jsonl`, resolve an ask,
complete or reorder work, configure a collector, merge, or deploy. An
acknowledgement or snooze stops applying when new material evidence rotates the
item's `attentionKey`. Watched state survives upstream status changes.

The native app stores one revisioned, locked, atomic JSON document per
registered workspace under its private Application Support directory. The
workspace registration id—not a loopback port or path supplied by HTTP—is the
identity. Browser-only serving uses the same closed API with process-memory
`session` state. It survives a page refresh in that process but resets when the
server process exits and never claims durable persistence.

Local notes are private one-line triage text. They are excluded from the public
projection, Copy Context, notifications, diagnostics, logs, backups, and
authoritative files.

## Today and the Decision Deck

Today can open one named authoritative source. For an admitted owner decision,
that action is **Open Decision Deck**. Today does not reproduce Answer, Resolve,
Complete, Skip, reorder, merge, or deploy controls.

The Decision Deck continues to support decision choice, recommendation
acceptance, need-more-info, authoritative snooze, manual-action completion or
skip, and digest-bound decision undo where safe. Its existing mutation routes
remain separate from the private local-state routes.

Set `ui.readOnly` to `true` for observer or imported-snapshot workspaces. The
service still returns validated orientation and local presentation state, while
every authoritative mutation route returns `403` before invoking a writer.

## First run, empty, and degraded behavior

- **First visit:** shows the latest 20 valid changes as Recent activity and
  offers to set a local starting point. It does not fabricate a previous visit.
- **No workspace:** says “No ledger is open” and routes to the launcher.
- **Empty success:** names the exact observation time and coverage that support
  the empty result.
- **Filter empty:** says the current filter has no matches; it is distinct from
  an empty workspace.
- **Partial/degraded:** keeps trustworthy rows visible and names each affected
  source or scope.
- **Invalid projection:** contains the failure. Any last-successful content is
  labeled historical rather than current.
- **Refresh:** keeps the last successful content visible with `aria-busy`; it
  does not invent skeleton counts or prose.
- **Browser-only:** supports session triage state but not native menus, durable
  state across process restarts, file watching, Dock badges, or native window
  restoration.

## Keyboard and native menus

The served page works without the native host. In the Mac app, the same
commands are also available through normal HFLedger, File, Edit, View, Item,
Window, and Help menus.

| Key | Behavior |
| --- | --- |
| Up/Down | Select the previous/next visible row across sections. |
| Left/Right | Collapse/expand a group or move between its header and members. |
| Return or `O` | Open the one supported source, otherwise focus the inspector. |
| `E` | Acknowledge the current attention generation locally, with Undo. |
| `S` | Open the local snooze surface. |
| `W` | Set watch/unwatch through private local state. |
| Command-F | Filter the current destination only. |
| Command-K | Open the command reference/palette. |
| Command-1…5 | Today, Changes, All Work, Shipped Log, Watched. |
| Escape | Close the topmost transient surface and restore its originating focus. |

Unmodified shortcuts do not fire inside editable controls. Selection moves to
a nearby row or section when a local action hides the selected row; focus never
falls silently to the document body. Native menu commands cross into the page
as one allowlisted command id. The board window receives no general Tauri IPC,
shell, or filesystem capability.

The native host watches only already configured, allowlisted board, ledger,
collector, and adapter report files. It rejects symlinks, debounces write
bursts, and refreshes without a primary Refresh button, recursive discovery,
polling, `git pull`, or collector enablement. The Today and Dock badges use the
visible attention total; observer failure becomes a coverage alert, not a
reassuring zero.

## Public engine and private adapters

The public engine understands generic sources, items, runs, changes, evidence,
links, diagnostics, provenance, and source health. Project-specific board
sections, repositories, paths, run names, and source mappings belong in a
private adapter that emits those generic records through exact stable ids.

Adapters and collectors are bounded, read-only observation inputs. They cannot
grant work, merge, deploy, send, decision, configuration, or filesystem
authority. HFLedger never uses title similarity or model judgment to associate
records. Public documentation, fixtures, screenshots, and release artifacts use
only fictional data.

## HTTP boundary

`GET /api/board?context=ID` returns the existing `orientation` object unchanged
and a separate `orientationV2` object during migration. It also reports whether
local state is `durable`, `session`, or `unavailable`.

Private presentation state uses only:

```text
GET  /api/local-state?context=<allowlisted-id>
POST /api/local-state/command
```

Commands are closed absolute set operations with an expected revision. The
request cannot provide a filesystem path, workspace id, replacement document,
decision outcome, completion, evidence object, collector setting, or ledger
action. All stateful reads use `Cache-Control: no-store`.

Existing authoritative routes remain documented in the protocol and Decision
Deck implementation. They use a separate registry and are blocked together by
`ui.readOnly`; the local-state route can modify only private presentation state.

## Security and deferred capabilities

The service keeps hard loopback binding, Host-header defense, no CORS opt-in,
JSON framing/body limits, explicit static allowlisting, CSP/frame/MIME/referrer
headers, text-only rendering of user-authored prose, closed route registries,
stale-card hashes, locked board/ledger writers, and atomic replacement.

V1 does not ship transition-based attention notifications, a rich menu-bar
status UI, Quick Look, effectiveness analytics, advanced dispute discovery,
multi-machine skew detection, global search, custom deep links, cloud triage
sync, authoritative Today write-back, notarized publication, updater delivery,
remote serving, collector auto-enable, or private authoritative cutover. The UI
contains no permanent empty controls that imply these are available.
