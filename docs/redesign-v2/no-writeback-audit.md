# HFLedger redesign V2 — no-write-back audit

Date: July 19, 2026

Base: accepted V1 integration commit
`ea00fc1801f3e426c92ccb4e02d8644edd75558f`

Audit branch: `audit/redesign-v2-no-writeback`

## Recommendation

Keep Today authoritative-data read-only, but do not treat the accepted V1 app
as having passed this boundary audit. Today itself has no direct authoritative
POST, menu command, notification action, or Tauri IPC capability. The audit did,
however, reproduce three core no-write-back failures in the surrounding owner
boundary: mixed-context read-only policy can be bypassed, Today can hand a
decision to the wrong context, and Decision Deck undo changes the board through
a path that cannot be replayed from the ledger. External link resolution and
native accelerators are additional Prompt 22/release blockers. Defense-in-depth
gaps also remain in copied advisory text and an isolated deferred menu-bar
branch.

Do not add authoritative write-back to Today. A future action should be
considered only when there is evidence of a compelling repeated need and the
action can call one existing admitted event path with explicit authorization;
it must not create a parallel Today-specific mutation path.

Prompt 22 must not integrate deferred work, and a release candidate must not
claim the no-write-back gate, until all findings marked as release blockers
have fixes and the expected-failure witnesses pass normally. The fixes belong
to a separate implementation task; this branch remains an audit and test
artifact only.

## Findings

| Severity | Finding | Evidence and required disposition |
| --- | --- | --- |
| High — release blocker | Mixed-context `ui.readOnly` bypass | `Runtime` copies `read_only` from the primary home only, while each `Context` loads its own config. The shell and `Handler.do_POST` use the primary flag. A writable primary mounting a read-only secondary advertises the secondary as writable and accepts an authoritative resolution. Enforce one fail-closed service invariant at startup or guard/render from the selected context. |
| High — release blocker | Decision Deck undo is not event-replayable | `/api/cards/undo` directly mutates `BoardStore`, then appends audit-only `deck_undo`. Replaying the same final ledger from the pre-resolution board leaves the decision resolved while the live board is reopened. Replace undo with a protocol-backed reconcile action or remove it; no direct board-only outcome transition. |
| High — release blocker | Today may open the wrong context's Decision Deck | Orientation hardcodes `/deck?context=main`; `deck.js` initializes from localStorage and does not consume the URL query. A decision selected in another context may open `main` or the previously stored context. Generate the selected context target and make the deck honor it before localStorage. |
| High — Prompt 22/release blocker | Source opener bypasses the contracted resolver | The canonical contract says the UI must not execute projection targets directly and that a server/native opener owns the allowlist. `safeLinkTarget` instead lets the client open any projected HTTP(S) target, including userinfo or another loopback service. This is navigation, not an HFLedger POST, but HFLedger cannot prove that an external GET has no effect. Route through the contracted resolver and reject local-network/userinfo destinations unless explicitly allowed. |
| Medium — trust clarity | Copy Context is not durably self-identifying | The projected text begins `HFLedger context`, not `non-authoritative` or `advisory`; the warning exists only in the transient toast. Projection also copies every `copyable` link target without applying the UI safe-target policy. Mark the copied bytes themselves and filter/qualify copied links. |
| High — Prompt 22/release blocker | Native E/S/W/O accelerators bypass the page's editable/modal key guard | Native menu accelerators dispatch the static page command even while an editable control owns focus. The resulting effect is local triage or navigation, not authoritative write-back, but it violates the interaction contract and can cause unintended local changes. Gate native receipt/eligibility on editable/modal state or remove unmodified native accelerators. |
| High for Prompt 15 integration | Deferred menu-bar window lacks command-level window authorization | Prompt 15 gives its app-owned `menu-bar` window `core:default`; Tauri custom handlers are globally registered. Packaged menu-bar JS invokes only narrow `menu_bar_*` handlers, but a compromised window could attempt launcher handlers. Add per-window authorization in each handler or a capability design that proves those invokes impossible before Prompt 22 selection. |
| Low | Disabled native menu placeholders diverge from the no-empty-controls contract | Group, find-next/previous, and reset-triage entries are constructed disabled with no dispatcher. They are not hidden authority, but should be removed or implemented as named safe behavior. |
| Medium — documentation/coverage drift | Native watcher scope is narrower than documented | V1 watches `board.json`, `ledger.jsonl`, and optional `reports/collector-latest.json`; docs claim configured collector and adapter reports. Correct the claim or add an exact configuration-derived allowlist before relying on automatic refresh coverage. |

## Scope and method

This audit read the protocol, UI contract, canonical redesign contract, Today
and Decision Deck clients, loopback server, private local-state backend, native
host menus/IPC/capabilities, and their tests. It reviewed the accepted V1 tree
as the release boundary and inspected the isolated deferred branch tips for
notifications, menu-bar status, Quick Look, effectiveness review, disputes,
machine skew, and search/deep links. Those deferred commits are observations,
not accepted-V1 features and not integration approval.

The proof combines four independent checks:

1. capability and route inventory;
2. static client/native allowlist assertions;
3. disposable-workspace HTTP tests that snapshot `config.json`, `board.json`,
   and `ledger.jsonl` before and after each local operation; and
4. read-only tests that exercise every authoritative POST route and assert
   `403` plus byte identity.

Expected-failure witnesses encode each reproduced blocker as the desired safe
behavior. They let the rest of the audit suite execute without pretending the
gate is green. An expected failure is an unresolved finding, not acceptance.

All dynamic tests use fictional data and temporary roots. No private adapter,
workspace, path, credential, or source excerpt is used.

## Authority planes

| Plane | State it may change | Entry path | Today access |
| --- | --- | --- | --- |
| Authoritative board | tasks, owner tasks, decisions, queue/materialized state | validated `BoardStore` transaction backed by admitted events where required | none |
| Authoritative ledger | decisions, completions, owner outcomes, audit events | registered writer plus append/reconcile contract | none |
| Installation configuration | contexts, collectors, writer registry, UI read-only policy | installation administrator/native launcher | none |
| Private presentation state | seen cursors, acknowledgement, local snooze, watch, selection, panes, disclosure | closed `/api/local-state/command` envelope | yes |
| Clipboard/navigation | bounded advisory text or one allowlisted destination | browser clipboard or normal navigation | yes; no authority grant |
| Native host administration | app settings, workspace registration, engine lifecycle, private backup copies | launcher window or tray, not the served board window | none through Today IPC |

The intended separation is largely structural, not a label:

- `app/server.py` keeps `POST_ROUTES` and `LOCAL_POST_ROUTES` in different
  registries.
- Every authoritative route hits a runtime `read_only` guard before a writer is
  invoked, but the guard is incorrectly service-global rather than selected-
  context-specific; this is the first blocking finding above.
- The local route receives the local-state backend, the closed context set,
  and the request body; it does not receive a `Runtime`, `Context`,
  `BoardStore`, ledger writer, collector controller, shell, or deployment
  client.
- `core/local_state.py` imports no board, ledger, reconcile, collector, Git,
  process, network, or native module. Its only durable write target is the
  app-private `state.json` selected by trusted launch arguments.
- The served board window is a loopback `WebviewUrl::External` window and is
  excluded from the Tauri capability assigned to the launcher window.

These facts prove that the local-state endpoint itself has no authoritative
writer and that accepted V1 Today has no direct Tauri invoke. They do not cure
the cross-context policy bug or the Decision Deck's direct undo mutation.

## Today/local-state operation map

| User-visible behavior | Exact implementation path | Permitted effect | Authoritative effect |
| --- | --- | --- | --- |
| Load/refresh | `GET /api/board` | read validated projection | none |
| Read local state | `GET /api/local-state` | read private/session state | none |
| Mark successful visit | `record-successful-visit` | view cursor and exact seen change ids | none |
| Mark changes seen | `mark-changes-seen` | exact private seen ids | none |
| Acknowledge | `acknowledge-attention` | hide one material attention generation locally | none |
| Snooze locally | `snooze-attention` | local expiry, at most 30 days | none |
| Clear local triage | `clear-attention-triage` | remove local acknowledgement/snooze | none |
| Watch/unwatch | `set-watch` | private watched membership | none |
| Select view/project/item | `set-navigation` | private navigation restoration | none |
| Resize panes | `set-pane-widths` | bounded private widths | none |
| Expand disclosure | `set-disclosure` | allowlisted private disclosure key | none |
| Copy Context | client `buildCopyContext` plus clipboard write | bounded plain advisory text | none; copied text is not an event or capability |
| Open source/decision | `safeLinkTarget` plus browser navigation | same-origin `/deck` or explicit HTTP(S) navigation | no HFLedger POST; external GET side effects are not currently containable |

The local command envelope is exact: schema version, allowlisted context,
optimistic revision, closed command id, and command-specific exact arguments.
Top-level or nested path, workspace id, replacement document, actor, action,
decision result, completion, evidence, collector setting, shell command, URL,
merge, or deployment fields are rejected. Projection-sensitive commands also
validate item ids, change ids, cursors, and attention keys against the current
projection before the private transaction.

Browser-only mode uses the same command implementation over a process-memory
backend. It creates no localStorage, cookie, conventional state file, or
cross-process durability claim. Native mode changes only the backend location:
the launcher supplies an app-private root and persisted workspace registration
id at process start. HTTP cannot choose either value. This local-state parity
passes; service-wide read-only parity does not pass for mixed context configs.

## Authoritative Decision Deck boundary

The following routes are authoritative and intentionally remain outside
Today:

| Route | Authority | Admitted path |
| --- | --- | --- |
| `/api/decisions/resolve` | append decision resolution and reconcile | owner Decision Deck/owner UI registry |
| `/api/decisions/snooze` | authoritative decision lifecycle snooze | owner Decision Deck/owner UI registry |
| `/api/decisions/reorder` | reorder the owner decision lane | validated board transaction plus UI audit event |
| `/api/tasks/done` | change an exact owner-task state | validated board transaction plus UI audit event |
| `/api/tasks/reorder` | reorder exact owner tasks | validated board transaction plus UI audit event |
| `/api/cards/answer` | choose/accept, complete/skip, need-info, or snooze | Decision Deck, registered event/completion gate |
| `/api/cards/undo` | digest-bound short-window decision undo | Decision Deck only, but currently direct-board and replay-divergent — blocker |

`app/static/deck.js` is the only served client that names
`/api/cards/answer` or `/api/cards/undo`. `app/static/app.js` names no
authoritative route. The Decision Deck also checks read-only state in the
client. In a single-context workspace the server returns `403` before any
writer is called. That statement is not true for a read-only secondary context
mounted by a writable primary, because the handler consults only the primary
flag.

An admitted decision opened from Today is intended to be a handoff. Navigation
to `/deck` does not itself answer it, but the current hardcoded context can show
the wrong card set. A later owner interaction can answer through the registered
path; undo is the documented exception that must be repaired.

## Links, Copy Context, and untrusted input

Projection links are data, not executable instructions. Today does not insert
link targets into HTML or POST them as commands. It currently accepts:

- the same loopback origin with path exactly `/deck`;
- `https:`; or
- explicit `http:`.

`javascript:`, `file:`, arbitrary same-origin API paths, malformed values, and
unsupported schemes render unavailable. Opening an accepted source is normal
navigation in a new `noopener,noreferrer` context, except the same-origin
Decision Deck handoff. It does not POST, invoke Tauri, interpolate script, or
open a local `file:` target. However, direct HTTP(S) navigation can target
userinfo or another loopback service, and an external GET is outside
HFLedger's transaction proof. The canonical resolver requirement is therefore
not fully implemented.

Copy Context is at most 4,000 characters of plain, bounded text. It excludes
local notes and does not carry an actor identity, authorization, event envelope,
route, IPC command, or executable object. The UI toast reports that copying
grants no authority, but that statement is not present in the copied bytes and
does not travel with a paste. Copied link targets also need the same policy as
visible links. A person or agent receiving current text must still use the
normal admission and writer gates, but the artifact should say so itself.

## Native, menu, shortcut, and notification audit

The native bridge accepts only a fixed `NativeCommand` enum and injects one
static `hfledger:native-command` event. It accepts no page-supplied script,
path, URL, body, writer, or command string. The corresponding client dispatch
table contains views, current-view filter/reload, pane display, open, local
acknowledge/snooze/watch, Copy Context, and help. Unknown ids are ignored or
reported unavailable. The direct-mutation check passes; the native accelerators
still need the editable/modal guard described in Findings.

The Item menu has no Answer, Resolve, Complete, Skip, Approve, Mark shipped,
Run command, merge, or deploy item. `item.reset-triage`, group, and find-next/
previous placeholders are constructed disabled and have no dispatch mapping;
they cannot be used as a hidden action route. The keyboard map is the same
closed client table. Unmodified item keys are suppressed in editable controls.

The current V1 notification is a privacy-bounded notice that a new owner card
exists. It has no action button or mutation callback. The Dock badge and menu
eligibility are reads of projection/private state. The file watcher only emits
a reload after an allowlisted file changes; it does not pull, collect, edit,
merge, or deploy.

The tray's Restart Local Engine and Create Backup entries are native-host
administration, not Today actions. Restart changes process lifecycle. Backup
copies the three core files into the app-private backup area and restarts the
engine; it does not edit the workspace copies. These commands are owned by the
launcher/tray host and are not exposed through the external board window's
Tauri capability.

## Deferred branch review

The accepted V1 commit contains none of the deferred Quick Look, rich menu-bar,
weekly-effectiveness, advanced-dispute, machine-skew, global-search, or custom
deep-link controls. The following isolated tips were reviewed only to keep a
later Prompt 22 integration from bypassing this audit:

| Capability | Reviewed tip | Boundary result |
| --- | --- | --- |
| Transition notifications | `87811d69e7e2f7ed891159a8f27b1b6500b7b9c5` | writes only private notification observation/navigation through `/api/local-state/command`; no authoritative callback. Replace its generic native `post_json(port, path, …)` helper with an exact local-state wrapper or prove the path statically before integration. |
| Rich menu-bar popover | `200bc09457b6743e6c8323455de02a09c7f69b59` | UI uses Today/private triage only, but the menu-bar window's access to globally registered launcher invokes needs per-window authorization before integration |
| Quick Look evidence | `0d3afa4536970a2f3bae9590fa9946f6b9e6ec1f` | bounded projected preview only; no new POST/native IPC, but preview source buttons inherit accepted V1's resolver blocker |
| Weekly effectiveness | `4796ee83113c9d2f60ee01de643fe75f50949413` | pure derived prototype outside Today with GET-only fictional server; production integration still needs a bounded read contract |
| Deterministic disputes | `a15b5c4eeda398c78dd09a03884550842f9d8445` | derived exact contradiction records; resolution remains a source handoff |
| Machine skew | `0942190d642eaebb0f7994f21891239833e495d1` | no fetch/pull/push/ref/file mutation; bounded `git ls-remote` occurs only for explicit validated remote refresh ids and requires a visible network-authority gate at integration |
| Search and deep links | `2600c1376e9d2b645adbdc011fbac0fb2a88e935` | no authoritative mutation; cold-link routing can start/switch the engine and writes app-private registration/navigation, so “navigation-only” does not mean zero local writes. Re-test running/cold/stale/malformed lifecycle hashes. |

These results do not select or merge any capability. Prompt 22 must rerun the
no-write-back suite after each selected cherry-pick because branch isolation is
not proof that their combined command/menu registrations remain safe.

## Pressure test: proposed Today actions

| Proposed button | Duplication/divergence risk | Safe handoff |
| --- | --- | --- |
| Approve | Creates a second decision-answer client and risks bypassing admission, option ids, stale-card hash, provenance, and undo rules | Open the exact admitted card in Decision Deck |
| Mark shipped | Confuses a local assertion with independent shipment evidence and can diverge from queue/repository/deployment state | Open the named source; the authorized runtime files a typed shipment event and independent observation verifies it |
| Resolve | Collapses task, decision, dispute, and completion semantics into one ambiguous write | Open Decision Deck for owner outcomes or the named authoritative source for external state |
| Run command | Gives untrusted projected text process/filesystem/network authority and makes read-only observation executable | Copy bounded context or a documented command for an explicit user-run terminal handoff; never execute it from Today |
| Ask agent to do it | Creates a second intake/dispatch surface that can bypass Ready-for-Build admission, protected gates, and runtime ownership | Copy Context into the chosen agent workflow; that workflow must intake/admit and execute through its own authority |

The problem is not button placement. Each proposal would make Today a second
writer with its own retries, identity, authorization, stale-state behavior,
error recovery, and provenance. The resulting copies could disagree while
both looked authoritative.

## Bar for any narrowly safe future action

A future authoritative action must prove all of the following before product
approval:

1. repeated evidence that navigation/handoff is materially inadequate;
2. one already-authoritative event or source operation, with no Today-specific
   state machine or direct board edit;
3. explicit actor identity and current authorization, with protected/manual
   gates preserved;
4. exact target identity, stale-state/replay protection, idempotency, and
   bounded arguments;
5. event-first provenance and atomic failure that leaves board and ledger
   byte-identical;
6. read-only, browser/native, untrusted-input, notification, deep-link, and IPC
   tests proving no alternate path;
7. a visible confirmation that names the authoritative effect and rollback;
8. independent security/product review plus explicit integration selection.

Even with that proof, the preferred design is a handoff to the Decision Deck
or named source. The default remains no authoritative Today write-back.

## Regression gate

`tests/test_no_writeback_boundary.py` is the focused Prompt 21 audit suite. It is
deliberately broader than the pre-existing representative tests:

- every accepted local-state operation is executed against a disposable
  read-only workspace and checked after the operation for configuration,
  board, and ledger byte identity;
- local-state envelope/argument smuggling and unknown commands fail closed;
- every authoritative route is exercised in read-only mode and checked for
  `403` plus byte identity;
- Today contains no authoritative endpoint, browser storage, arbitrary invoke,
  or action command;
- Decision Deck ownership of authoritative answers is asserted separately,
  with expected-failure witnesses for mixed-context policy, context handoff,
  and replay-divergent undo;
- source navigation, Copy Context, native capability scoping, static command
  allowlists, notification/menu/tray behavior, and disabled placeholders are
  pinned.

Prompt 22 and every future release candidate should run this focused test, the
full Python suite, UI tests, Rust tests, and the release/privacy check. This
audit is not green while any witness remains an expected failure. Any new Today
POST path, native command, notification action, deep-link action, or
authoritative-file byte change is also a release blocker pending a new explicit
boundary review.

The audit run completed 10 focused cases with four expected failures, the full
213-case Python suite, the repository release check, all 12 Rust tests, and
Rust clippy with warnings denied. The external publish privacy gate was not
configured for this local run. An expected failure is a recorded defect, so
successful process exit is not a product-acceptance signal for this gate.
