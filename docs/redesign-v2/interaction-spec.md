# HFLedger redesign v2 — interaction, visual, and accessibility contract

Date: July 18, 2026  
Status: Wave 1 design contract  
Base commit: `ca27e60a9bff1f15c4f553edae707df37c47b497`

This document specifies the served Today experience inside the HFLedger Mac
board window. It is a product and presentation contract, not an implementation
or projection schema. Exact data shapes, ranking inputs, coverage thresholds,
and local-state storage belong to the sibling Wave 1 contracts and the final
synthesis contract.

## 1. Product job and boundaries

HFLedger is a restrained Mac ledger browser. Its primary loop is:

> What changed since I last looked, and what needs me now?

The interface should let a returning user orient in seconds, then inspect the
evidence behind any statement without scanning the whole board. It is not a
metrics dashboard, an editable task manager, or another agent chat.

The design combines five reference patterns without copying any source product:

- Things: quiet source-list navigation, system typography, a curated Today,
  flat rows, and completed work receding into a log.
- Linear: sidebar → triage list → persistent inspector, compact selection, and
  view changes that preserve the application shell.
- Tower: chronological history, durable selection, and dense evidence that is
  legible because it lives in the inspector rather than the main list.
- Sentry: attention ordering with an explicit explanation instead of a hidden
  score.
- Flighty: lead with the outcome or delta, then explain the cause and any
  uncertainty in plain language.

### Authority boundary

The Today board is read-only with respect to authoritative tasks and decisions.
It may change private local presentation state only:

- mark a change seen;
- acknowledge an attention item locally;
- snooze an item locally;
- watch or unwatch an item locally;
- remember the current view, selection, pane widths, filters, and disclosure.

These actions must never update `board.json`, append an authoritative outcome,
move queue state, answer a decision, complete a manual action, or imply that
work is done. “Acknowledge” means “remove this interruption from my local Today
for now,” not “resolve.” “Snooze” means “hide locally until this date,” not
“change the authoritative due date.”

The inspector may open an authoritative source, including the existing
Decision Deck when that is the source of an owner ask. The Decision Deck
remains the only HFLedger surface that answers owner decisions. Today never
embeds answer, resolve, mark-done, reorder, or authoritative snooze controls.

## 2. Application shell

### 2.1 Wide-window structure

At a content width of 1,120 points or more, use a persistent three-pane split
view below a compact unified title/toolbar area:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ traffic lights   HFLedger — Workspace          [Filter] [Commands] [•••]   │
├────────────────┬──────────────────────────────────┬─────────────────────────┤
│ TODAY        2 │ Today                            │ Selected item           │
│ Changes      5 │                                  │                         │
│ All Work       │ NEEDS YOU                    2   │ Why it is here          │
│ Shipped Log    │ ───────────────────────────────  │ Duration                │
│ Watched        │ ◆ Blocks tonight's run · 2d     │ Next action             │
│                │ ◆ Waiting on review · 6d         │ [Open pull request]     │
│ PROJECTS       │                                  │ [Copy Context]          │
│ Ovenlight      │ NEW SINCE LAST VISIT         5   │                         │
│ …              │ Overnight run · 6 changes        │ Evidence                │
│                │   › Search fix shipped · 4h      │ Missing observations    │
│                │   › Mobile QA changed · 4h       │ Two clocks              │
│                │                                  │ Item history            │
│                │ QUIET CONCERNS               1   │ Source links            │
│                │ ○ Silent while observed · 3d     │ ▸ Runtime & provenance  │
│                │                                  │                         │
│ board ✓ · gh ! │ 12 parked or unobserved →        │                         │
│ observed 2h   │                                  │                         │
└────────────────┴──────────────────────────────────┴─────────────────────────┘
```

The sidebar is a source-list, the center is the active ledger view, and the
right pane is a read-only dossier for the current selection. Panes share 1-point
dividers. They do not sit inside rounded cards.

### 2.2 Toolbar

The toolbar is quiet and contextual:

- The window title is `HFLedger — {workspace}`; the active destination appears
  as the center-pane heading, not as a marketing hero.
- A compact filter control exposes Command-F and filters the active destination
  only.
- A command button exposes the same command list as Command-K. It searches
  commands, not ledger content.
- A trailing overflow menu contains refresh, view options, copy-context when
  available, and pane visibility. It must not become the primary navigation.
- Read-only authority is stated in Settings/About and in relevant inspector
  copy; a permanent “read-only” pill is unnecessary because no authoritative
  mutation controls exist here.
- The toolbar has no metric cards, decorative brand lockup, large refresh
  button, or “Open deck” call-to-action. Opening the Decision Deck is the named
  next action only when the selected item requires it.

### 2.3 Sidebar destinations

Navigation order is fixed:

1. Today
2. Changes
3. All Work
4. Shipped Log
5. Watched
6. Projects

Today shows a badge only when its Needs You total is nonzero. Changes shows a
badge only for unseen changes. Badges use tabular numerals, read `99+` above
99, and have VoiceOver labels such as “Today, 2 items need you.” All Work,
Shipped Log, and Watched do not display totals by default; totals belong beside
section headings after the user opens a view.

`PROJECTS` is a subdued group label followed by project rows in stable display
order. Selecting the `Projects` destination opens the project index. Selecting
a project row opens its scoped ledger. Long project lists scroll independently
without moving the pinned coverage footer.

The selected destination uses the system accent selection material. Hover and
keyboard focus remain visually distinct from selection. Destination glyphs
are monochrome SF Symbols or equivalent semantic system glyphs; project rows
may use a small neutral folder or cube glyph.

### 2.4 Coverage footer

The sidebar footer is always pinned below navigation and projects. In a healthy
state it is one or two quiet lines:

```text
board ✓ · github ✓ · files —
observed 18m ago
```

Each source has a symbol plus text or an accessible label; color is never the
only signal. Clicking the footer selects a coverage dossier in the inspector.
When the observer or enough relevant coverage is degraded, stale, unavailable,
or never observed such that Today claims are invalidated, the footer remains
visible but the problem also becomes the first selectable meta-alert in Today.
A global coverage failure must never be communicated only through this footer.

Exact state thresholds and wording come from the coverage contract. The UI
must preserve these distinctions rather than collapsing them to online/offline.

## 3. Today

Today is a curated check-in, not a full inventory. Its order is invariant:

1. observer meta-alert, only when global coverage is broken enough to affect
   the screen;
2. ranked and capped Needs You;
3. New Since Last Visit, grouped by stable run;
4. at most three quiet-while-observed concerns;
5. a quiet footer linking to relevant library lists.

```text
TODAY

[! Observation is stale — agent activity has not been checked for 9h]  optional

NEEDS YOU                                                        2 of 9
◆ Approve the release gate
  Blocks the next attended release; decision is still open        2d  Verified

◇ Review the disputed shipment
  Reported shipped, but the repository still shows an open PR      6h  Disputed

                                               Show all 9 in All Work →

NEW SINCE LAST VISIT                                                   5
▾ Overnight grind · 4:11 AM · 4 changes
  ✓ Search result fix shipped
    Merged and corroborated by repository history                    4h  Verified
  ↗ Mobile QA moved to review
    Agent reported a passing check and requested review              4h  Agent-reported

▾ Sweep · Yesterday, 7:20 PM · 1 change
  • External review began
    Haresh was recorded as the next reviewer                         1d  Inferred

QUIET CONCERNS                                                    1 of 7
○ Storage follow-up is silent
  No activity for 3d while issue and agent sources were observed     3d  Verified

12 parked or unobserved items are quiet in the library →
```

### 3.1 Observer meta-alert

The meta-alert is a full-width flat row before Needs You, not a banner above
the app. It contains a warning glyph, a short consequence-first title, one
reason line, the affected-source summary, and a relative observation time. It
opens a coverage dossier in the inspector. It does not push global totals into
cards or repeat every source error inline.

Use one meta-alert even when several sources are affected. The inspector lists
each source separately. If only an item-specific source is absent, name that
absence in the item inspector instead of escalating the whole screen.

### 3.2 Needs You

- Presentation is capped at seven rows by default. The heading states
  `7 of {total}` when capped and offers “Show all” into the corresponding All
  Work smart list.
- Ranking is supplied by the deterministic projection contract. The interface
  must not re-sort with a model, a numeric confidence score, title matching, or
  a hidden “recommended” algorithm.
- A selected item stays visible until the user explicitly acknowledges or
  snoozes it locally, the projection removes it, or its authoritative state
  changes. Merely opening the inspector does not acknowledge it.
- Pressing `E` locally acknowledges the row. The row leaves Today, focus moves
  to the next logical row, and a brief status message offers Undo. The source
  remains unchanged and the item remains findable in All Work.
- Pressing `S` opens the local snooze popover. A snoozed row leaves Today until
  its local date is due or new authoritative evidence invalidates the snooze,
  as resolved by the local-state contract.

### 3.3 New Since Last Visit

- Changes are grouped by a stable run id, never by fuzzy title or merely by
  adjacent timestamps. A run can be a sweep, grind, agent session, import, or
  another typed upstream run.
- Groups are newest first. Within a group, the projection's deterministic
  order is preserved.
- A run header contains the human-readable run type/name, end time, count, and
  disclosure control. It is focusable and announced as a group.
- Groups begin expanded on Today. A user collapse is remembered locally.
- An item is marked seen only according to the local-state contract; scroll
  position alone must not silently mark off-screen changes seen. Opening or
  explicitly acknowledging a visible change is an acceptable seen trigger.
- First-run behavior does not claim “since last visit” when no visit cursor
  exists. See section 9.

### 3.4 Quiet Concerns

- Show at most three concerns on Today, ordered by the projection's explicit
  precedence and tie-breakers.
- The phrase “quiet” is legal only if all sources relevant to the silence claim
  were successfully observed for the stated interval.
- If sources are disabled, stale, unavailable, or never observed, use
  “unobserved,” not “quiet,” and route the item to All Work rather than this
  section.
- The row reason states both the silent duration and the fact of observation,
  for example: “No activity for 3d while issue and agent sources were
  observed.”

### 3.5 Quiet footer

The final Today line is a low-emphasis library link, not a status card. It may
summarize parked and unobserved totals, for example “12 parked or unobserved
items are quiet in the library.” It opens All Work with the relevant smart
lists in view. It never says “all clear” unless the coverage-qualified
empty-success rules are satisfied.

## 4. Center-row contract

Every selectable ledger item uses the same anatomy:

```text
[glyph]  Title                                      [relative time] [provenance]
         One reason line                [one project OR one agent label] [watch]
```

Required elements:

1. **Semantic glyph.** A 14–16 point SF Symbol or equivalent; its shape
   communicates the primary presentation state. Semantic color may tint only
   this glyph and a tiny unread marker.
2. **Title.** One line, medium weight, sentence case. It identifies the durable
   work item or change, not a generated category.
3. **Reason.** Exactly one visible line explaining why the row is here. It is
   not the raw task description.
4. **Optional label.** At most one project or one agent/runtime label, chosen by
   whichever adds more disambiguation in the active view. Never show both.
5. **Relative time.** The underlying item's last-change clock, not the last
   collector poll. Hover/help text and the accessibility description expose
   the exact timestamp.
6. **Provenance marker.** One of `Verified`, `Agent-reported`, `Inferred`,
   `Unobserved`, or `Disputed`, rendered as a small glyph-plus-word label. Do
   not use a numeric confidence or an unlabeled colored dot.

Rows are flat and divided by 1-point separators. Default row height is 58–66
points depending on label presence. The title and reason each truncate once
in the center list; the full bounded text is available in the inspector and
VoiceOver description. There are no nested cards, shadows, progress charts,
multi-row tag clusters, or more than one metadata pill.

### 4.1 Primary glyph vocabulary

| Meaning | Suggested system glyph | Visible word where needed |
|---|---|---|
| Needs owner action | diamond/exclamation | Needs you |
| Disputed claim | exclamation arrows/diamond | Disputed |
| Quiet while observed | hollow circle or pause | Quiet |
| Shipped but unverified | open check circle | Unverified |
| In motion | arrow up-right or pulse | In motion |
| Shipped and corroborated | checkmark circle | Verified |
| Parked | archive box | Parked |
| Unobserved | eye slash | Unobserved |
| Run group | chevron plus run-kind glyph | Sweep, grind, or session |
| Coverage problem | warning triangle | Coverage problem |

The projection assigns one primary home. The UI does not blend glyphs or place
an item in two conflicting Today sections. Watched, seen, acknowledged, and
snoozed are local overlays, not competing primary homes.

## 5. Inspector dossier

The inspector makes an evidence-backed argument. It is not a generic property
grid. For a selected item, render sections in this order:

```text
[primary glyph]  Item title
                 Project or authoritative id

WHY IT IS HERE
Waiting for review because implementation is complete and no reviewer
activity has been observed for two days.

DURATION
Waiting for 2 days

NEXT ACTION
[Open pull request]                       one authoritative action only
[Copy Context]

EVIDENCE
✓ Merge check passed                         GitHub · observed 38m ago
↗ Agent reported implementation complete     Codex · reported 2d ago
! No review event observed                    GitHub · observed 38m ago

MISSING OBSERVATIONS
Claude activity is unavailable. File changes are not configured.

FRESHNESS
Item changed          Tuesday at 2:14 PM
Sources observed      GitHub 38m · board 12m · Claude unavailable

HISTORY
Today 10:42 AM        Repository check corroborated open review
Tuesday 2:14 PM       Agent requested review
Tuesday 1:58 PM       Tests passed (agent-reported)

SOURCES
Open authoritative task · Open pull request · Reveal artifact

▸ Runtime & provenance internals
```

### 5.1 Header and why-here

- Show the full bounded title, semantic glyph, one identity line, and local
  watched/snoozed state.
- “Why it is here” must name the presentation rule in plain language. It may
  include the provenance term but never exposes a score.
- Duration describes time in the current meaningful state, distinct from both
  freshness clocks. Omit it when the projection cannot determine a start.

### 5.2 One next action

Expose exactly one computed next action when one is supported. Its label names
the destination: “Open pull request,” “Open Decision Deck,” “Open issue,” or
“Reveal report,” not “Take action.” Opening an authoritative source is allowed;
answering or mutating it inside Today is not.

When no supported action exists, say “No next action is supported by the
observed evidence.” Do not invent advice. Local Acknowledge, Snooze, and Watch
remain separate secondary controls and are never presented as the work's
authoritative next action.

### 5.3 Copy Context

`Copy Context` creates bounded plain text suitable for pasting into an agent
chat. It includes:

- title and stable public item identifier;
- why the item appears here;
- current presentation state and provenance word;
- relevant evidence summaries and timestamps;
- named missing observations;
- the next action and stable source links that are safe to share.

It excludes private file contents, secrets, raw untrusted excerpts, hidden
runtime payloads, local preference notes unless the user explicitly includes
one, and any claim stronger than the inspector displays. Copying announces
“Context copied” through a nonintrusive status region. The action is available
from the inspector, Item menu, context menu, and Command-K.

### 5.4 Evidence rows

Evidence is chronological newest first by default and uses shape plus text:

- claim summary;
- evidence kind;
- named source;
- source reference when safe;
- item-change timestamp;
- source-observed timestamp;
- provenance tier.

The row distinguishes “reported at” from “observed at.” It never turns an
agent report into a checkmark merely because it is recent. Disputed evidence
shows the conflicting claims adjacent to one another and identifies which
source said each thing.

### 5.5 Named missing observations and two clocks

The inspector always names relevant unavailable evidence rather than using a
generic “coverage partial” message. If nothing relevant is missing, the section
may collapse to “Relevant sources observed.”

Freshness always separates:

1. **Item changed:** when the underlying item last changed.
2. **Sources observed:** when each relevant source was last successfully read.

Relative time is followed by an exact local timestamp on demand and in the
accessibility value. Never label a recent collector poll as a recent item
change, or an old item as stale merely because it has not changed.

### 5.6 History, sources, and internals

- Item history is a vertical, chronological ledger. It includes meaningful
  state/evidence events, not every UI click or collector heartbeat.
- Source links use stable descriptive labels and an external-link indicator.
  Broken or unsafe links render as unavailable text rather than guessed URLs.
- Runtime, raw source identifiers, hashes, adapter notes, and provenance
  internals are collapsed by default. Expanding them does not reveal secrets,
  private file contents, or unbounded untrusted prose.

## 6. Destination wireframes

The shell, row anatomy, selection, inspector, and authority boundary remain
constant across destinations.

### 6.1 Today

Today uses the section order and caps in section 3. Its center heading contains
the coverage-qualified as-of line, not totals as cards:

```text
Today
Observed through 10:42 AM across 3 of 4 relevant sources

[meta-alert if globally necessary]
Needs You
New Since Last Visit
Quiet Concerns
[library footer]
```

### 6.2 Changes

Changes is the complete reverse-chronological journal, grouped by stable run.

```text
Changes                                              5 unseen
[Filter changes…]                         [All ▾] [Mark visible seen]

▾ Overnight grind · Today 4:11 AM                           4
  [change row]
  [change row]
  [change row]
  [change row]

▸ Sweep · Yesterday 7:20 PM                                11
▸ Codex session · Yesterday 3:04 PM                         3
```

- Default scope is all change kinds; the compact filter can narrow by project,
  run kind, or seen state without changing deterministic chronology.
- Unseen changes use a leading unread indicator plus an accessibility label.
  Seen rows do not become low-contrast or disabled.
- “Mark visible seen” affects only rows currently included by the filter and
  requires an Undo status message. There is no destructive “clear journal.”
- A run header opens a run dossier summarizing its sources, observation window,
  and members. A child row opens the item dossier.
- Changes may contain items also queryable elsewhere; this is a journal view,
  not a second primary-home classification.

### 6.3 All Work

All Work is the library and exposes the projection's mutually exclusive homes.

```text
All Work                                                   84
[Filter this view…]                         [Project ▾] [View ▾]

SMART LISTS
Needs You 9 · Disputed 2 · Quiet 7 · Shipped unverified 3
In Motion 12 · Shipped verified 38 · Parked 8 · Unobserved 5

DISPUTED                                                     2
[row]
[row]

QUIET WHILE OBSERVED                                         7
[row]
…
```

- Smart lists are counts and filters, not dashboard cards. They can be a
  compact pop-up, segmented list, or single wrapping line at normal zoom; they
  must become a menu at narrow widths.
- An item appears in one primary smart list according to the projection
  precedence. Local watched/snoozed/acknowledged filters can overlay this
  classification without changing it.
- The default ordering inside each smart list is deterministic and explained
  in the view-options popover. User-selectable alternatives may be title,
  item-change time, or project only if the projection contract supports them.
- Totals state the complete data count even when rendering is virtualized or
  paged.

### 6.4 Shipped Log

The Shipped Log is a chronological record of corroborated outcomes.

```text
Shipped Log                                                38
[Filter shipped work…]                  [All projects ▾] [Newest ▾]

TODAY
✓ Search contrast fix
  Merged to stage and corroborated by repository history      4h  Verified

YESTERDAY
✓ Observer bridge installed
  Installed artifact passed the named verification            1d  Verified
```

- Default content is `shipped-verified` only. Shipped claims that are merely
  agent-reported, inferred, disputed, or unobserved stay in their higher-cost
  primary homes and are accessible through All Work.
- A clearly named optional filter may show “All shipment claims,” preserving
  provenance wording. It must not make unverified claims look logged as fact.
- Group by local calendar day, then use item-change time descending and stable
  id as a tie-breaker.
- Completion decoration is quiet; no celebratory gradients, confetti, large
  green areas, or success metric cards.

### 6.5 Watched

Watched is a local-only collection layered over authoritative state.

```text
Watched                                                     6
Private to this Mac · authoritative work is unchanged

NEEDS YOU
★ [row]

IN MOTION
★ [row]

SHIPPED VERIFIED
★ [row]
```

- Rows retain their primary-home glyph and provenance. The watch star is a
  secondary local marker.
- Group by current primary home in precedence order; do not invent a new
  watched status.
- Unwatching removes the row from this view after a brief Undo opportunity.
- If a watched item disappears upstream, keep a bounded tombstone only as long
  as the local-state contract allows and label it “No longer in the current
  projection.”

### 6.6 Projects index

Projects is an index, not a portfolio dashboard.

```text
Projects                                                    4
[Filter projects…]

[folder] Ovenlight
         2 need you · 5 changed since last visit · observed 18m ago

[folder] Example Mobile
         No attention items · coverage partial
```

Each project row contains name, one short orientation line, and coverage-aware
observation time. Do not add progress percentages, health scores, charts, or
metric cards. Selecting a row opens the project destination.

### 6.7 Project destination

```text
Ovenlight
Observed through 10:42 AM across all relevant sources
[Filter Ovenlight…]                           [Open source] [View ▾]

NEEDS YOU                                                     2
[row]

RECENT CHANGES                                                5
[run-grouped rows]

OTHER WORK                                                   17
[rows grouped by one-home classification]
```

The project view is a scoped orientation page: Needs You first, recent changes
second, then the remaining library. It uses the same global precedence and
coverage rules as Today. “Open source” appears only when the project has a
stable authoritative source link.

## 7. Local interaction states

No state may rely on color alone.

| State | Visual treatment | Behavior and accessibility |
|---|---|---|
| Selected | System-accent source-list/list selection, full contrast | Persists across view refresh when the stable id remains; announced “selected” |
| Hover | Neutral translucent row fill, no accent bar | Pointer-only affordance; never substitutes for focus |
| Keyboard focus | 2-point high-contrast focus ring or inset outline | Visible in both appearances and distinct from selection |
| Seen | Normal title and reason; unread marker absent | Still selectable and full contrast; announced only if queried |
| Unseen | Small leading unread shape plus normal text weight | Announced “unseen”; color-independent |
| Acknowledged | Absent from local Today; visible in library with local check/label in inspector | Never displayed as resolved or done; can be reset locally |
| Snoozed | Clock glyph and “until {date}” in filtered/library views | Hidden from Today until due; VoiceOver reads the exact local date |
| Watched | Star glyph at trailing edge or inspector header | `W` toggles; announced “watched” |
| Disputed | Conflict/exclamation glyph plus `Disputed` provenance word | Conflicting evidence is adjacent in inspector; no red-only treatment |
| Stale source | Clock/warning glyph and `Stale` source word | Item clock remains separate; inspector gives last successful observation |
| Degraded coverage | Warning glyph, named source, consequence-first reason | Global failure escalates to first Today row; item-specific absence stays in dossier |

Selected, hover, and focus can coexist. Focus wins the outline, selection wins
the fill, and hover adds no extra effect when a row is already selected.

## 8. Keyboard and menu model

Keyboard commands work when focus is in the ledger shell. Unmodified letter
shortcuts are disabled while typing in a field, filter, dialog, or editable
control. Every shortcut appears in a menu item and in Command-K.

### 8.1 Core keyboard behavior

| Key | Behavior |
|---|---|
| Up / Down Arrow | Move selection to the previous/next visible row across section boundaries |
| Left Arrow | Collapse the selected run/section; if already collapsed, move to its group header |
| Right Arrow | Expand the selected run/section; from a group header, move to its first row |
| Return | Open the selected row's one authoritative source; if none exists, move focus into the inspector |
| O | Open the selected authoritative source; disabled with a named reason when unavailable |
| E | Acknowledge the selected item locally and offer Undo |
| S | Open the local snooze popover for the selected item |
| W | Toggle local watched state |
| Command-F | Focus a filter scoped to the current destination |
| Command-K | Open the command palette and keyboard reference; command search only |
| Command-1 … Command-5 | Open Today, Changes, All Work, Shipped Log, and Watched respectively |
| Escape | Close the topmost popover/palette/filter; on narrow layouts, return from the inspector; otherwise focus the selected sidebar destination |

When an action removes the selected row, selection moves to the next row, then
the previous row, then the nearest section header. Focus never falls back to
the document body. A local action announces its result and Undo availability.

### 8.2 Complete Mac menus

The native host must provide normal Mac application menus even though the board
content is served from a loopback URL. Menu commands route to the focused board
window when applicable.

**HFLedger**

- About HFLedger
- Settings… (`Command-,`)
- Services
- Hide HFLedger (`Command-H`), Hide Others, Show All
- Quit HFLedger (`Command-Q`)

**File**

- Open Workspace…
- Open Authoritative Source (`Return`, alternate `O` shown in the Item menu)
- Refresh Sources (`Command-R`)
- Close Window (`Command-W`)

**Edit**

- Undo / Redo when a local action supports it
- Cut, Copy, Paste, Select All using standard system behavior
- Copy Context (`Command-Shift-C`) when an item is selected
- Find in Current View… (`Command-F`)
- Find Next / Previous (`Command-G` / `Command-Shift-G`)

**View**

- Today (`Command-1`)
- Changes (`Command-2`)
- All Work (`Command-3`)
- Shipped Log (`Command-4`)
- Watched (`Command-5`)
- Show/Hide Sidebar (`Command-Control-S`)
- Show/Hide Inspector (`Command-Option-I`)
- Expand/Collapse Selected Group
- Actual Size (`Command-0`), Zoom In (`Command-+`), Zoom Out (`Command--`)
- Enter/Exit Full Screen (`Control-Command-F`)

**Item**

- Open Authoritative Source (`O`)
- Acknowledge Locally (`E`)
- Snooze Locally… (`S`)
- Watch / Unwatch (`W`)
- Copy Context (`Command-Shift-C`)
- Reset Local Triage State… when applicable

The menu label changes to reflect the selected item's current local state. An
unavailable command is disabled and includes a help tag explaining why; it
does not silently fail.

**Window**

- Minimize, Zoom, Move & Resize, and standard macOS window commands
- Bring All to Front
- HFLedger workspace windows, with a checkmark for the focused window

**Help**

- HFLedger Help
- Keyboard Shortcuts (`Command-K` opens the command reference)
- Privacy & Read-only Model
- Show Diagnostics

The existing tray/menu-bar item may retain simple Show, Restart Engine, Backup,
and Quit commands. A rich menu-bar popover is deferred and must not be required
for the core interaction contract.

## 9. Loading, empty, and failure states

All states preserve the application shell and use a compact center-pane
message. They do not replace the app with an illustration or marketing page.

### Loading

- Keep the last successfully rendered data visible when refreshing, add a
  small progress indicator beside the as-of line, and mark it `aria-busy`.
- On first load, show 4–6 neutral row skeletons with no fabricated titles,
  counts, or status colors.
- Motion is a subtle opacity pulse only and is removed under Reduce Motion.

### Empty success

Use only when coverage is sufficient for the relevant claim:

```text
Nothing needs you right now
Observed through 10:42 AM across all relevant sources.
[Review recent changes]
```

“Nothing needs you” is forbidden without the coverage-qualified as-of line. An
empty Needs You section does not imply no unobserved work exists.

### First run / no visit cursor

Do not say “New since last visit.” Use:

```text
Recent activity
This is your first visit on this Mac. Showing the latest 20 observed changes.
[Set this as my starting point]
```

Establishing the cursor is a local action. It does not edit the workspace.

### No board / no workspace

```text
No ledger is open
Choose an initialized HFLedger workspace in the launcher.
[Open Workspace…]
```

If a configured workspace no longer contains a readable board, say exactly
that and offer “Open Workspace…” and “Show Diagnostics.” Do not initialize,
repair, or overwrite data from this state without an explicit launcher flow.

### No results

```text
No items match “release” in Watched
[Clear filter]
```

This is distinct from empty success and from an empty underlying destination.

### Degraded source

Keep trustworthy rows visible. Put the global meta-alert first, qualify the
as-of line, and name missing sources in each affected dossier. Never replace
the whole interface with an error if the validated board projection remains
readable.

### Error

For an unreadable or invalid projection:

```text
HFLedger could not read this ledger
The validated board response was unavailable. Last successful view: 10:42 AM.
[Try Again] [Show Diagnostics]
```

If safe cached display is supported, label it “Last successful view” and make
its age prominent. Never present cached state as current. Raw stack traces and
filesystem paths stay in Diagnostics, not the main interface.

## 10. Responsive and pane-collapse behavior

Pane widths are adjustable through split-view dividers and persisted per
workspace. Persist logical widths, not dynamic loopback origins.

| Available content width | Layout |
|---|---|
| 1,120+ pt | Sidebar + center list + persistent inspector |
| 820–1,119 pt | Sidebar + center list; inspector becomes a trailing overlay/drawer opened by selection |
| 600–819 pt | Center list only; sidebar is a toolbar popover and inspector replaces the center with a visible Back control |
| Below 600 pt | Window stops shrinking horizontally; content never compresses into illegible columns |

Rules:

- Wide defaults: sidebar 210 pt, center at least 430 pt, inspector 360 pt.
- Adjustable bounds: sidebar 180–320 pt; inspector 320–560 pt; center always
  retains at least 400 pt at 100% zoom when three panes are visible.
- When narrowing, collapse the inspector first, then the sidebar. Never collapse
  the center ledger.
- Re-expanding restores the user's last valid pane widths. Values outside new
  bounds after an upgrade are clamped, not discarded.
- An inspector overlay traps neither keyboard nor VoiceOver focus. Escape and
  the visible Close/Back control return focus to the originating row.
- At narrow widths, row title, reason, relative time, and provenance remain.
  The optional project/agent label is the first element allowed to hide; its
  content remains in the accessibility description and inspector.
- At 200% zoom, follow the same collapse rules rather than clipping text or
  introducing horizontal page scrolling.

The Mac board window should be allowed to shrink below its current 900-point
minimum only after the two-pane and replacement-inspector behaviors pass QA.

## 11. Visual system

### 11.1 Tokens

| Token | Contract |
|---|---|
| Font family | `-apple-system`, BlinkMacSystemFont, SF Pro Text/Display fallbacks; no serif in the board |
| Center title | 22/28 pt, semibold |
| Section heading | 11/14 pt, semibold, system secondary label; restrained uppercase allowed |
| Row title | 13/17 pt, medium |
| Reason/body | 12/16 pt, regular |
| Metadata | 11/14 pt, regular; tabular numerals for counts/time |
| Source reference | 11/15 pt, SF Mono only when the value is actually code/id |
| Spacing | 4, 8, 12, 16, 24 pt scale |
| Row height | 58–66 pt normal; grows with text zoom rather than clipping |
| Divider | 1 physical pixel / hairline using system separator color |
| Corner radius | 6 pt controls, 8–10 pt popovers; 0 for panes, rows, and sections |
| Accent | System accent for selection/focus; HFLedger purple for brand mark and links only |
| Semantic color | Green, orange, red only on small status glyphs; always paired with shape/text |
| Motion | 100–140 ms opacity/position for disclosure and drawers; no spring or decorative motion |

### 11.2 Light appearance

- Content canvas uses the system window background or a neutral warm white no
  warmer than the existing paper background.
- Sidebar uses source-list material or a faithful system-material approximation
  within the loopback WebView.
- Rows are transparent until hover, focus, or selection.
- Dividers use system separator color; primary text uses label color; reason
  text uses secondary label color.

### 11.3 Dark appearance

- Use system dark window and source-list materials, not a purple/black branded
  theme.
- Maintain hierarchy through label colors and dividers, not glowing borders.
- Semantic glyph colors are tuned independently for dark contrast.
- Evidence code/id fields use a neutral elevated fill only where needed; whole
  sections do not become floating cards.

### 11.4 System accent and increased contrast

Selection, focus, standard controls, and links must respond to the user's system
accent where WebView/native APIs allow it. If the served page cannot read the
accent, use a semantic system accent CSS color before falling back to HFLedger
purple. Increased Contrast strengthens dividers, focus rings, and selection
edges without adding shadows or changing meaning.

### 11.5 Explicit removals

The redesigned board contains none of the following:

- serif hero or oversized project name;
- equal-weight metric cards;
- full-width coverage banner;
- pill-shaped top navigation;
- nested rounded panels and cards;
- more than one metadata label on a center row;
- gradients, glows, decorative shadows, or backdrop decoration;
- large semantic-color surfaces;
- opaque recommendation scores or numeric confidence.

## 12. Accessibility contract

### 12.1 Semantics and VoiceOver

- Use native/HTML landmarks for navigation, toolbar, main list, and inspector.
- Each center section has a real heading and count. Each run header exposes
  expanded/collapsed state and controls its child list.
- A ledger row is one focusable selection target using listbox/option or an
  equivalent proven macOS WebView pattern. Do not make every text fragment a
  tab stop.
- Row accessible names follow: “{title}. {reason}. Changed {relative time}.
  {provenance}. {unseen/watched/snoozed if applicable}.” Exact timestamps are
  available in the description.
- Glyphs that duplicate adjacent words are hidden from assistive technology.
  Glyph-only controls have explicit names, states, and keyboard equivalents.
- The inspector is labeled “Details for {title}.” When opened as a narrow
  overlay, focus moves to its heading; closing returns focus to the row.
- Local actions and refresh results announce through a polite live region.
  Read failures use an assertive alert once, not on every retry.

### 12.2 Focus order

On entry, focus restores to the selected row if it still exists; otherwise it
goes to the active sidebar destination. Tab order is:

1. toolbar controls;
2. sidebar destination list and coverage footer;
3. active center row/group selection target;
4. inspector actions, disclosures, and source links.

Arrow navigation handles movement within sidebar and row collections. Focus
never jumps into the inspector solely because selection changed on a wide
window; the user explicitly tabs, presses Return when no source exists, or
opens the inspector in a collapsed layout.

### 12.3 Contrast and color independence

- Normal text meets at least 4.5:1 contrast; large text meets 3:1.
- Focus indicators and meaningful non-text controls meet 3:1 against adjacent
  colors.
- Selected text retains required contrast for every supported macOS accent.
- Every semantic color has a glyph shape and visible or accessible word.
- Seen/unseen, healthy/degraded, and verified/disputed are distinguishable in
  grayscale.

### 12.4 Text size, zoom, and motion

- Use rem/system-relative units so 200% browser zoom remains usable with no
  lost controls, clipped reason text, or horizontal page scroll.
- At large text, rows grow and panes collapse according to section 10.
- Do not fix inspector sections to pixel heights; long bounded content scrolls
  in the pane.
- Respect Reduce Motion by removing pulses, animated scrolling, and drawer
  transitions. Loading remains perceivable through text and a static progress
  glyph.
- Do not autoplay sound, flash content, or animate status glyphs.

## 13. Content style for reason lines

Reason lines answer “why is this row here?” rather than repeat the title.

### Formula

> **State or outcome** because **observed cause**, with **missing evidence**
> stated when it changes interpretation.

Rules:

- sentence case, plain language, active voice where the source supports it;
- one sentence or sentence fragment, one visible line, target 55–100 characters;
- lead with the operational consequence: `Blocks`, `Waiting`, `Changed`,
  `Shipped`, `Silent`, `Unobserved`, or `Disputed`;
- name the human, agent, source, or gate only when it helps the next action;
- use explicit provenance words in the marker rather than hedging adverbs;
- say “No activity was observed” rather than “Nothing happened”;
- say “The GitHub source is unavailable” rather than “GitHub looks broken”;
- never use encouragement, celebration, blame, anthropomorphism, “AI thinks,”
  numeric confidence, or an unexplained severity score;
- never expose raw untrusted prose, stack traces, hashes, or file paths in the
  reason line.

Good:

- `Blocks tonight's run because the required owner decision is still open.`
- `Waiting for review; implementation is complete and no review event was observed for 2d.`
- `Shipped after the merge and deployed artifact were both corroborated.`
- `Unobserved because GitHub and agent activity sources are unavailable.`
- `Disputed: the agent reported shipment, but the pull request remains open.`

Avoid:

- `This task may possibly be stuck (72% confidence).`
- `Great news — the amazing agent crushed this release!`
- `No activity.`
- `P1 · Needs Review · Codex · GitHub · 2d` as a substitute for explanation.

## 14. Component inventory

| Component | Responsibility | Required variants/states |
|---|---|---|
| `LedgerWindow` | Unified title/toolbar and split-view shell | light, dark, increased contrast, narrow |
| `SourceSidebar` | Destinations, projects, pinned coverage | selected, hover, focus, collapsed popover |
| `DestinationRow` | Glyph, label, optional badge | selected, count 0/1/99+ |
| `CoverageFooter` | Compact source health and last observation | healthy, partial, stale, unavailable, never observed |
| `ViewToolbar` | Heading, as-of line, scoped filter, view options | filtering, refreshing, degraded |
| `SectionHeader` | Section title, visible/total count, disclosure | expanded, collapsed, capped |
| `RunHeader` | Stable run label, time, count, disclosure | seen mix, expanded/collapsed |
| `LedgerRow` | Required center-row anatomy | all primary homes plus local overlays |
| `ProvenanceMarker` | Glyph and required vocabulary word | verified, agent-reported, inferred, unobserved, disputed |
| `MetaAlertRow` | Global observer problem | degraded, stale, unavailable |
| `LibraryFooter` | Link from Today to parked/unobserved library | count, zero/hidden |
| `InspectorDossier` | Evidence argument for selection | item, run, project, coverage |
| `NextAction` | One authoritative outbound action | supported, unavailable with reason, absent |
| `CopyContextAction` | Bounded safe clipboard content | ready, copied, unavailable |
| `EvidenceRow` | Claim/source/two timestamps/provenance | corroborating, reporting, conflict, missing |
| `FreshnessClocks` | Item-change and per-source-observed clocks | current, stale, never observed |
| `HistoryTimeline` | Meaningful per-item events | empty, bounded, expanded |
| `SourceLinkList` | Stable authoritative links | available, broken/unavailable |
| `LocalTriageControls` | Acknowledge, snooze, watch | default, acknowledged, snoozed, watched |
| `SnoozePopover` | Local until-date and optional local note | invalid date, saved, cancelled |
| `ScopedFilter` | Filter current destination only | inactive, active, no results |
| `CommandPalette` | Commands and shortcut discovery | search, no command match |
| `StatusMessage` | Action result and Undo | polite success, error, undo timeout |
| `EmptyState` | Compact state-specific guidance | success, first-run, no-board, no-results |
| `LoadingState` | Honest loading without fake data | initial, refresh over prior data |
| `ErrorState` | Safe recovery and diagnostics path | unreadable, stale cached fallback |

## 15. Deferred extension points

The following are explicitly outside this contract's required implementation:

- Quick Look or Space-bar evidence preview;
- a rich menu-bar popover or primary menu-bar workflow;
- native notifications beyond existing host behavior;
- advanced agent-effectiveness analytics, charts, trends, or scorecards;
- global search across all workspaces or all evidence;
- remote/cloud triage-state sync;
- answering decisions or authoritative write-back from Today.

The shell reserves no permanent empty controls for these features. Command-K is
command discovery, and Command-F is current-view filtering; neither should be
misrepresented as deferred global search.

## 16. Measurable acceptance criteria

### Structure and content

1. At 1,280 × 820 points, the default board presents sidebar, center list, and
   inspector with no horizontal page scrolling.
2. Today renders sections in the required order. Needs You shows no more than
   seven rows and Quiet Concerns no more than three; headings expose complete
   totals when capped.
3. New Since Last Visit groups changes only by stable run id and never calls a
   first-run list “since last visit.”
4. Every item row has one glyph, one title, one reason line, no more than one
   project/agent label, one item-change relative time, and one provenance word.
5. No item appears in two conflicting primary homes in the same projection.
6. The inspector renders why-here, duration when known, one next action, Copy
   Context, evidence, named missing observations, two clocks, item history,
   source links, and collapsed runtime/provenance internals in that order.
7. Verified, agent-reported, inferred, unobserved, and disputed are written as
   words wherever provenance is shown. Numeric confidence is absent.
8. A global coverage failure appears as the first Today meta-alert; an
   item-specific absence appears in that item's dossier.

### Authority and privacy

9. Today contains no control or code path that resolves a decision, completes
   a manual action, reorders authoritative work, mutates `board.json`, or calls
   an authoritative mutation endpoint.
10. Acknowledge, snooze, watch, seen cursor, selection, disclosure, and pane
    width survive an app restart and dynamic-port change without entering the
    authoritative workspace data.
11. “Open Decision Deck” or another source action opens the authoritative
    surface; it never embeds an answer form in Today.
12. Copy Context excludes raw file contents, secrets, untrusted HTML, private
    preference notes by default, and claims stronger than the visible dossier.

### Keyboard and menus

13. A keyboard-only user can switch all five primary views, traverse every
    visible row, expand/collapse runs, open a source, acknowledge, snooze,
    watch, filter, open Command-K, close overlays, and reach every inspector
    control without a focus trap.
14. Command-1 through Command-5 map exactly to Today, Changes, All Work,
    Shipped Log, and Watched.
15. The Mac menus contain every command in section 8, show shortcuts, update
    dynamic Item labels, and disable unavailable commands with an explanation.
16. When a local action removes a selected row, focus lands deterministically
    on the next logical target and the result is announced.

### Responsive and appearance

17. Snapshot/manual checks pass in system light, system dark, increased
    contrast, and at least two non-purple system accent colors.
18. At widths of 1,120, 900, 720, and 600 points, pane collapse follows section
    10; the selected item and return focus survive every transition.
19. At 200% zoom, all destinations remain operable without clipped controls or
    horizontal page scrolling, and center rows retain title, reason, time, and
    provenance.
20. Pane widths restore per workspace after restart and are clamped safely when
    the window becomes narrower.
21. The board contains no serif typography, metric cards, full-width coverage
    banner, gradients, glows, decorative shadows, or nested rounded panels.

### Accessibility

22. Automated and manual contrast checks meet 4.5:1 for normal text and 3:1
    for large text, focus indicators, and meaningful non-text controls.
23. Every semantic state is distinguishable without color and in grayscale.
24. VoiceOver announces destination badges, run disclosure, row reason,
    item-change time, provenance, local overlays, inspector identity, and action
    results without duplicate glyph noise.
25. Reduce Motion removes all nonessential transitions and pulsing while
    leaving loading and state changes understandable.
26. Full keyboard and VoiceOver passes succeed in both the wide three-pane and
    narrow replacement-inspector layouts.

### Six-second orientation test

Using fictional fixtures and a cold participant who understands only that
HFLedger tracks agent work, each test screen is a pass only if at least 4 of 5
participants can point to the correct answer within six seconds for each of:

1. What needs me now?
2. What changed since the last visit?
3. Why is the selected claim believed, reported, disputed, or unobserved?
4. When did the item change, and when were its sources last observed?
5. What is the one useful next action?

No participant may need to scan All Work, interpret a color without a label, or
open raw runtime internals to answer. A screen that says “nothing needs you”
without a coverage-qualified as-of statement fails automatically.

## 17. Decisions locked by this contract

Unless the synthesis contract explicitly changes them, implementation agents
must treat these as locked:

1. HFLedger is a three-pane Mac ledger browser centered on Attention and
   Changes, not a dashboard.
2. Today is ordered meta-alert → capped Needs You → run-grouped changes → at
   most three quiet concerns → library footer.
3. A center row shows one reason, at most one project/agent label, the
   item-change clock, and an explicit provenance word.
4. The inspector is an evidence argument with named gaps and two clocks, not a
   property grid.
5. Local acknowledge/snooze/watch/seen state is mutable; authoritative task and
   decision state is not.
6. System typography, source-list styling, flat rows, dividers, one accent, and
   small semantic glyphs replace the current hero/cards/banner/pills aesthetic.
7. Keyboard behavior is first-class and every command is discoverable in full
   Mac menus and Command-K.
8. Narrow windows collapse inspector first and sidebar second while preserving
   selection and returning focus to the originating row.
9. Light/dark appearance, system accent, Reduce Motion, VoiceOver, contrast,
   and 200% zoom are release requirements rather than polish.
10. Quick Look, rich menu-bar UI, notifications, advanced analytics, and global
    search remain deferred extension points.
