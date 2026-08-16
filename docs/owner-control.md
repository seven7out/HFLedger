# Owner control, calendar, and operations

HFLedger gives a non-developer owner one durable place to shape what agents work
on without asking the owner to manage branches, checks, or implementation state.
The control plane has three surfaces: **Priorities** for owner-authored product
direction, **Calendar** for dated work and scheduled runs, and **Operations**
for visibility into commands and recurring jobs across agents and local automation.

## What the owner controls

For a task, the owner may set:

- a concise owner-facing headline;
- a product section for scanning related work;
- the product outcome agents should pursue;
- why that outcome matters to people;
- the plain-language condition that means the work is done;
- two to twelve independently finishable product outcomes when the source task
  bundles unrelated results;
- one short owner note;
- an optional plain need-by date;
- whether the task is active or parked; and
- its position in the active priority list.

These are owner directives. Agent-reported status, tests, releases, source
evidence, and technical history remain read-only. The app never presents those
fields as owner-editable progress controls.

An owner can report that an unsplit product outcome is complete, or split a
mixed task and mark each outcome complete independently. The report is one-way
and removes a fully completed task from the active priority order. It does not
change the observed task status, claim that code passed, or manufacture release
evidence. Completed work remains visible under **Completed by owner** with its
observed status alongside it.

An exact owner-only manual task is different: the owner is the person who knows
whether that action happened. Its inspector therefore offers **Mark complete**.
After confirmation, HFLedger appends a one-way `owner-task-complete` event and
removes the task from the active owner lane. It cannot complete a queue task,
claim an agent result, or be used to reopen a source-captured completion.

Dragging a task changes the durable priority order. It is not a personal sort
preference. The active order is available to agents through the CLI and generated
instruction packs, so an agent choosing work sees the same order as the owner.
Every edit is an append-only event with a revision; concurrent stale edits fail
and reload rather than silently overwriting a newer choice.

Priorities opens in **By section**, which groups related product work without
changing sequence. **Urgent** stays open at the top and contains exactly the
first five active items from the authoritative order; it is not a second stored
priority field. The visible rank numbers remain the exact agent order, and the
owner switches to **Exact order** to change urgent membership by dragging or
using accessible move controls. A
deterministic title-based organizer supplies editable starting sections for
existing work. The interface labels these as automatic; **Move…** opens the
section control, and an owner choice always replaces the suggestion. Starting
sections include UX & interface, Directory data, New features, Reliability &
automation, Safety & privacy, Content & outreach, Internal tools, Release &
operations, Research & planning, and Other product work. Custom product areas
remain valid.

Projection adapters that replace an observed workspace must preserve
`owner-control.jsonl` byte for byte. If the journal is invalid, Today remains
readable but owner editing and agent selection fail closed until the journal is
repaired; the app never guesses a replacement order.

## Two authority lanes

An installation may observe a board that another system owns. HFLedger therefore
keeps source facts and owner direction separate:

| Lane | Owns | May change when the observed board is read-only? |
| --- | --- | --- |
| Observed workspace | execution status, evidence, runs, releases, source facts | No |
| Owner control | owner headline, automatic or owner-chosen product section, intended outcome, importance, definition of done, need-by date, product-outcome split and completion reports, owner note, active/parked choice, priority order, owner-only manual completion report | Yes |

Owner events are stored in `owner-control.jsonl`; they never rewrite `board.json`
or `ledger.jsonl`. The projection overlays the latest valid directive while
retaining the original source title and status as provenance. Removing an
override returns the task to its observed value.

For an owner-only manual task, the projection overlays a completed state from
the one-way owner report while leaving the observed record untouched. An
installation adapter may reconcile `completedOwnerTaskIds` into its source only
through that source's sanctioned completion writer. It must not edit source
files directly or treat this report as proof of agent execution.

## Operations

Operations answers three owner questions in plain language:

1. What commands are available and what does each one do?
2. Which recurring jobs exist, who runs them, and when should they run?
3. Did the latest run succeed, fail, or stop reporting?

Job purpose and health are primary. The responsible agent or local runner,
model when known, cadence, and next run make each recurring responsibility
legible without exposing its implementation. Invocation text and evidence links
are secondary reference material. A failed run must include a one-sentence
product or process consequence; raw stack traces and secrets never appear in
the primary summary.

An optional, closed `reports/operations-latest.json` report supplies the command
catalog and recurring-job observations. It is read-only evidence: seeing a command or
job never grants authority to execute it, enable it, merge, deploy, or write
to production. Missing or stale observations say so directly and never appear as
a reassuring success.

The current report has version `2`, an `observedAt` timestamp, a bounded
freshness window, and closed `commands` and `schedules` lists. A command supplies
an id, product label, product description, and secondary invocation text. A
schedule supplies a closed runner object (`agent`, `local_automation`, or
`unknown`) with a display name and optional model, plus its cadence, enabled
state, optional command association, next run, and optional latest run. Latest
run status is one of `succeeded`, `failed`, `running`, `missed`, or `unknown`;
the engine derives the owner-facing Healthy, Problematic, Running, Unknown, or
Paused health state. Version `1` remains readable and is surfaced as **Runner
not reported**. Unknown fields and unsafe file permissions invalidate the report
as a unit; the app contains that failure as an Operations status instead of
refusing to show the rest of the workspace.

## Calendar

Calendar is a read-only month projection plus an agenda. It includes active
task need-by dates, open owner-decision deadlines, deferred items returning for
attention, and the next run of each enabled schedule. It intentionally excludes
created, updated, evidence, and history timestamps so ordinary activity does
not flood the owner's calendar.

An owner may add, change, or remove a task's **Need this by** date in the
Priorities edit sheet. That instruction is stored only in
`owner-control.jsonl`; an explicit removal can hide an inherited deadline
without changing the observed source. Decision deadlines and schedule times
remain read-only. Clicking dated work opens its Details; clicking a scheduled
run opens Operations. Calendar does not execute work, create external calendar
events, or expand an indefinite recurrence series.

## Surface map

- **Today** keeps production health, owner cards, and product flow first.
- **Priorities** provides a sectioned product overview, a separate exact-order
  drag mode, an automatic top-five Urgent group, parked work, and the task
  product-edit sheet. The sheet can replace unclear legacy wording with an
  owner-readable headline, outcome, importance, definition of done, and bounded
  independently completable outcomes. Completed product tasks remain in a
  collapsed history section.
- **Calendar** collects task dates, owner-response deadlines, returning items,
  and next scheduled runs in a month grid and agenda without inventing dates.
- **Operations** groups recurring jobs by agent or local runner and shows their
  model when known, next expected run, latest outcome, and health.
- **All Work** and the inspector show the effective owner title and intent while
  preserving observed status and source provenance.

All owner-authored prose is plain text, control-stripped, length-bounded, and
rendered without markup interpretation. Public fixtures use only fictional data;
installation-specific commands, schedule names, paths, and run details remain in
private workspace reports.
