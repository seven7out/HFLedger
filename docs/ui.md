# Local reference interface

HFLedger governs the interrupt channel between agents and a product owner who
is not expected to evaluate code. Its primary check-in loop is:

> Is production healthy, what product judgment needs me, and where is work in
> the path from idea to production?

The loopback service serves a Mac-oriented Today browser and the existing
phone-sized Decision Deck from the same validated board and append-only ledger.
There is no UI task database. Today is a read-only orientation projection; the
Decision Deck remains the owner outcome surface.

## Visual contract

Today, the Decision Deck, Settings, onboarding, and recovery use one restrained
light visual system. The three-pane Today layout is the anchor: quiet neutral
surfaces, thin separators, compact controls, and color reserved for state and
the configured workspace accent. Owner cards are dense product documents, not
glowing presentation cards.

HFLedger deliberately does not infer an alternate palette from the operating
system's dark-mode preference. A future theme may be added only as an explicit
owner setting with its own cross-surface visual review. Recovery must preserve
the same visual language as Today so a failed engine cannot make the app appear
to have changed products.

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

Today starts with the owner model in this exact order:

1. one plain production-health line: **Healthy**, or **Degraded** with a
   one-sentence product reason; when continuous monitoring is enabled, a quiet
   secondary line says when production was last checked;
2. cards awaiting the owner, grouped as idea picks, production outcomes, risk
   judgments, agent blockers, and priority reviews; and
3. product flow: **Ideas waiting on pick → Being specced → Being built → On the
   test site → Shipped to production**.

The test site is a proving ground and is explicitly allowed to break. A failure
there keeps neutral styling and says “Allowed to break.” Only production
degradation uses alarm styling.

Below that summary, Today remains ordered by consequence rather than a weighted
score:

1. one coverage meta-alert when a global observation gap invalidates the
   evidence view;
2. at most seven ranked attention rows;
3. changes since the last successful visit, grouped by exact run id;
4. at most three quiet-while-observed concerns; and
5. a link to parked or unobserved work in All Work.

The five owner card kinds are defined in [`owner-model.md`](owner-model.md).
Primary card fields are plain product language. Implementation-shaped material
is available only through secondary `footnoteLinks` or the evidence inspector.

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

The Decision Deck supports prepared product choices, recommendation acceptance,
need-more-info, authoritative snooze, stuck-alarm completion or skip, and
bounded priority-review reorder-and-kill outcomes. Product evidence links are
visible on outcome reviews; technical footnotes remain secondary. Existing
mutation routes remain separate from private local-state routes.

Set `ui.readOnly` to `true` for observer or imported-snapshot workspaces. The
service still returns validated orientation and local presentation state, while
every authoritative mutation route returns `403` before invoking a writer.

## First run, empty, and degraded behavior

- **First visit:** shows the latest 20 valid changes as Recent activity and
  offers to set a local starting point. It does not fabricate a previous visit.
- **No workspace:** says “No ledger is open” and routes to the native
  Workspaces section in Settings.
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
| Space | Toggle the bounded evidence preview for the selected row. |
| Return or `O` | Open the one supported source, otherwise focus the inspector. |
| `E` | Acknowledge the current attention generation locally, with Undo. |
| `S` | Open the local snooze surface. |
| `W` | Set watch/unwatch through private local state. |
| Command-F | Filter the current destination only. |
| Command-K | Focus app-wide search over bounded projected metadata only. |
| Command-1…5 | Today, Changes, All Work, Shipped Log, Watched. |
| Escape | Close the topmost transient surface and restore its originating focus. |

Unmodified shortcuts do not fire inside editable controls. Selection moves to
a nearby row or section when a local action hides the selected row; focus never
falls silently to the document body. Native menu commands cross into the page
as one allowlisted command id. The board window receives no general Tauri IPC,
shell, or filesystem capability.

The Today toolbar keeps Search and Settings separate. Search is an inline text
box whose popover contains only ledger-item matches. Settings uses one exact
native-intercepted loopback sentinel to replace Today with a capability-bounded
Settings child webview in the same window; the board page itself receives no
native IPC. Back and the Today menu restore the mounted Today webview. Settings
retains workspace management, engine recovery, notifications, Launch at Login,
Reopen Last Board, persistent text size, backups, Finder reveals, diagnostics,
and quit. Its own Global Search dialog is search-only, while the slash-command
reference lives in a separate Help dialog.

The native host watches only already configured, allowlisted board, ledger,
collector, and adapter report files. It rejects symlinks, debounces write
bursts, and refreshes without a primary Refresh button, recursive discovery,
`git pull`, or collector enablement. Separately, an owner may enable one
production-health check for a workspace in native Settings. That address stays
in private app data, requires HTTPS, follows no redirects, and never enters the
board or ledger. The monitor checks once per minute while HFLedger is running,
degrades after three consecutive failures, recovers after one success, and
retains no response body. The Today and Dock badges use the visible attention
total; observer failure becomes a coverage alert, not a reassuring zero.
The complete monitor contract is in
[`production-monitoring.md`](production-monitoring.md).

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

Quick Look adds only a Quick Look–styled in-app evidence preview over the
existing orientation V2 projection. It does not use macOS Quick Look, read
referenced files, fetch remote content, interpret markup, or add native
authority. Open Source requires the existing server-owned read-only link
resolver; unsupported evidence uses a fixed unavailable state, while the
existing inspector remains the deeper dossier.

Bounded local search and navigation-only installed-app item links operate only
over projected metadata and do not change those authority boundaries; see
[`redesign-v2/search-links.md`](redesign-v2/search-links.md).

V1 does not ship transition-based attention notifications, a rich menu-bar
status UI, effectiveness analytics, broader dispute analytics,
multi-machine skew detection, search beyond that bounded local surface, cloud
triage sync, authoritative Today write-back, notarized publication, updater
delivery, remote serving, collector auto-enable, or private authoritative
cutover. The UI contains no permanent empty controls that imply these are
available.
