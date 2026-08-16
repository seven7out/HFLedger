# Owner control and operations

HFLedger gives a non-developer owner one durable place to shape what agents work
on without asking the owner to manage branches, checks, or implementation state.
The control plane has two surfaces: **Priorities** for owner-authored product
direction and **Operations** for visibility into commands and scheduled work.

## What the owner controls

For a task, the owner may set:

- a concise owner-facing headline;
- a product section for scanning related work;
- the product outcome agents should pursue;
- one short owner note;
- whether the task is active or parked; and
- its position in the active priority list.

These are owner directives. Agent-reported status, tests, releases, source
evidence, and technical history remain read-only. The app never presents those
fields as owner-editable progress controls.

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
| Owner control | owner headline, automatic or owner-chosen product section, intended outcome, owner note, active/parked choice, priority order, owner-only manual completion report | Yes |

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
2. What scheduled work is enabled and when should it run?
3. Did the latest run succeed, fail, or stop reporting?

Command purpose and schedule outcome are primary. Invocation text, runtime names,
and evidence links are secondary reference material. A failed run must include a
one-sentence product or process consequence; raw stack traces and secrets never
appear in the primary summary.

An optional, closed `reports/operations-latest.json` report supplies the command
catalog and schedule observations. It is read-only evidence: seeing a command or
schedule never grants authority to execute it, enable it, merge, deploy, or write
to production. Missing or stale observations say so directly and never appear as
a reassuring success.

The report has version `1`, an `observedAt` timestamp, a bounded freshness
window, and closed `commands` and `schedules` lists. A command supplies an id,
product label, product description, and secondary invocation text. A schedule
supplies its cadence, enabled state, optional command association and next run,
plus an optional latest run with one of `succeeded`, `failed`, `running`,
`missed`, or `unknown`. Unknown fields and unsafe file permissions invalidate
the report as a unit; the app contains that failure as an Operations status
instead of refusing to show the rest of the workspace.

## Surface map

- **Today** keeps production health, owner cards, and product flow first.
- **Priorities** provides a sectioned product overview, a separate exact-order
  drag mode, an automatic top-five Urgent group, parked work, and the task
  product-edit sheet.
- **Operations** lists commands, schedules, their next expected run, latest
  outcome, and a bounded error summary.
- **All Work** and the inspector show the effective owner title and intent while
  preserving observed status and source provenance.

All owner-authored prose is plain text, control-stripped, length-bounded, and
rendered without markup interpretation. Public fixtures use only fictional data;
installation-specific commands, schedule names, paths, and run details remain in
private workspace reports.
