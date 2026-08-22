# Instruction packs, collectors, and installation

The automation layer adds read-only observation and generated operating
instructions around the HFLedger protocol. Collectors never edit the board or
append events. Packs explain how an agent should interpret observations and use
existing HFLedger commands; they are not executable policy engines.

## Agent evidence writers

Codex, Claude Code, and other runtimes can report progress through the same
bounded CLI contract:

```sh
ledger --home /path/to/private-ledger-data event started \
  --task task-example --runtime codex --summary "Implementing the parser"
ledger --home /path/to/private-ledger-data event verified \
  --task task-example --runtime codex --summary "Parser suite passes" \
  --evidence test "python3 -m unittest tests.test_parser"
ledger --home /path/to/private-ledger-data event shipped \
  --task task-example --runtime codex --summary "Parser merged" \
  --evidence commit abc1234
```

The command appends audit-only `agent-evidence-v1` events. It does not move a
queue item, resolve a decision, or authorize deployment. Runtime adapters
should emit a `started` event when work begins, sparse `checkpoint` events only
at durable milestones, `blocked` when progress truly stops, `verified` after a
named check, and `shipped` only with observable evidence.

## Automation configuration

The optional `automation` object in `config.json` has this closed shape:

| Field | Meaning |
| --- | --- |
| `ownerRole` | Neutral role name inserted into generated instructions. |
| `repositories[]` | Repository id, GitHub `owner/repository` slug, stage branch, and production branch. |
| `sources.github` | Enable switch and bounded limits for open PRs, merged PRs, runs, and issues. |
| `sources.localFiles.roots[]` | Local root id, path, relative glob patterns, and per-root file limit. |
| `sources.berd` | Optional CLI, session limit, freshness window, and exact session-to-task links. |
| `workPolicy` | Ready/review statuses, stage-merge eligibility, and the required `productionWrites: false`. |
| `packs.runtimes` | Unique selection of `generic` and `claude-code`. |
| `schedule` | Whether a generated schedule is intended and its local hour/minute. |

Existing data directories without `automation` still validate, but `collect` and `render-packs` require the object. Copy the neutral block from [`config.example.json`](../config.example.json), edit it, and run `ledger validate` before generating artifacts.

Sources are never discovered or enabled automatically. Configure each
repository and local root explicitly, authenticate `gh` outside HFLedger when
GitHub observation is wanted, run one attended collection, and inspect the
source-health result before scheduling another run. A configured source starts
as `never-observed`; an explicitly off source is `disabled`.

Queue automation fields are typed. `autonomousSafe`, when present, must be boolean. `repository`, `statusKey`, and `protectedClass`, when present, must be non-empty text. Generated work instructions treat every missing safety field as gated.

## Collectors

Run one collection with:

```sh
ledger --home /path/to/private-ledger-data collect
```

The command takes a nonblocking `locks/collector.lock` so scheduled runs cannot overlap. It writes private, mode-`0600` files under `reports/`:

- `collector-latest.json` is the machine-readable report and authoritative completion marker.
- `collector-latest.md` contains only source health and observation counts; external summaries are omitted.

Exit status is `0` for healthy or explicitly idle, `1` when any enabled source is degraded, `2` for invalid configuration or an operating error, and `3` when another collection holds the lock. A configured source failure is durable in the JSON report and never reported as healthy.

The owner-facing **Refresh now** button runs the same bounded collector after
the existing fail-closed reconciler, then reloads the validated selected
workspace. It does not add a second collection path: the collector lock still
prevents overlap, disabled sources remain disabled, and degraded sources remain
visible. The button is unavailable when the selected workspace is read-only.

The redesigned Today projection keeps collector health distinct from work
status. Source states are `disabled`, `never-observed`, `unavailable`,
`degraded`, `stale`, `idle`, or `healthy`. Only a complete current `idle` or
`healthy` observation can support an absence claim. A disabled collector,
latest-only report without the required window, stale result, or failed run
makes affected work unobserved; it never proves quiet.

### GitHub

The GitHub adapter calls an already authenticated `gh` executable with argument arrays, never a shell. It reads bounded lists of pull requests, workflow runs, and issues plus a production-to-stage branch comparison. It does not fetch comments, bodies, secrets, repository contents, or write endpoints.

Titles and workflow names become bounded single-line fields prefixed with `[untrusted]`. Other external strings are control-stripped and length-bounded. Counts and identifiers remain typed. A failure in one configured repository degrades the GitHub source even when other repository observations succeed.

Authenticate and inspect access separately with `gh auth status`. HFLedger does not store a GitHub token.

### Local files

The local adapter walks configured roots without following directory or file symlinks. It matches relative globs and records only the root id, a digest of the relative path, a bounded display summary of that path, extension, byte size, and modification time. It never opens or reads file contents. Missing or unreadable roots degrade the source; reaching `maxFiles` is reported through `truncatedRoots`.

### Berd sessions

The optional Berd adapter calls the Berd-bundled `berdctl` with argument arrays,
never a shell. It lists a bounded number of sessions and requests each session
with `--messages 0`. Only id, normalized process state, timestamps, an optional
exact configured task id, and secondary harness/model/agent labels survive
normalization. Conversation titles, project ids, working directories, message
counts, messages, and unknown fields are dropped.

The normalized mode-`0600` report has its own freshness window. A missing Berd
app or CLI, timeout, invalid response, partial read, or stale report stays
degraded or unknown. Working does not prove progress, waiting does not prove an
owner blocker, and stopped does not prove completion. See
[`berd-session-observer.md`](berd-session-observer.md) for the closed contract.

Collector reports are observations. An agent must not treat a title, path, status, or report change as authority to execute work, create an owner ask, merge, or deploy.

## Operations observation

The owner-facing Operations view reads optional private commands/schedules and
session reports. Both are closed observation contracts, not schedulers or
agent controllers. Installations may map their explicitly known
commands and recurring jobs into that report with product labels, product
descriptions, runner and model identity, cadence, enabled state, next expected
run, and the latest bounded outcome. Invocation text is secondary and
secret-shaped text is rejected.

Use `ledger operations` to inspect the same projection agents receive. Missing,
stale, invalid, failed, or missed reporting remains explicit. HFLedger never
discovers arbitrary machine commands, scans scheduler configuration, installs a
schedule, starts a process, or retries a failed task from this report.
Installation adapters may combine explicitly configured jobs from multiple
agent schedulers and local automation into one report.

## Installation adapters

The public engine accepts only generic normalized sources, items, runs,
changes, evidence, links, and diagnostics. An installation with project-specific
board sections, repositories, paths, or run names maps them in a private
read-only adapter. The adapter must use exact stable ids or an explicit exact
reference map; title similarity and prose parsing cannot associate evidence.

Keep private adapters and their outputs in the private data installation. Do
not copy them into the engine repository, public fixture catalog, screenshots,
diagnostics, or release artifacts. The public fictional catalog under
`tests/fixtures/redesign-v2/` documents the generic shapes and coverage cases
without providing a production-specific mapping.

An adapter that atomically replaces an observed workspace must carry the
independent `owner-control.jsonl` journal forward byte for byte. Regenerating a
board is not authority to discard or rewrite owner priorities.

If an installation reconciles owner-only manual completion reports back into
another authoritative system, it must consume `completedOwnerTaskIds` through
that system's sanctioned completion writer. HFLedger never runs that writer or
edits the observed source directly.

## Instruction packs

Render the configured runtimes into the private data directory:

```sh
ledger --home /path/to/private-ledger-data render-packs
```

The Codex adapter produces `AGENTS.md` plus
`prompts/ledger-{sweep,work,attend,status}.md` with `--runtime codex` evidence
instructions. The generic adapter produces `AGENTS.md` plus
`prompts/{sweep,work,attend,status}.md` with runtime `other`. The Claude Code
adapter produces `CLAUDE.md` plus
`.claude/commands/ledger-{sweep,work,attend,status}.md` with runtime
`claude-code`. Each work/attend pack records start, true blocker, verification,
and evidenced shipment milestones through the audit-only event surface. A
deterministic `manifest.json` records every relative path and SHA-256 digest.

Rendering is strict: templates may use only declared placeholders, unresolved tokens fail, and symlink targets are rejected. Existing generated files are not replaced unless `--force` is supplied. Generation does not copy files into a repository or a runtime-global configuration directory.

## Fresh installation

The Phase 3 installer creates a new data directory, validates and writes configuration, renders packs, and optionally generates inactive scheduler definitions:

```sh
./install/ledger-install /path/to/private-ledger-data \
  --project "Fictional orchard tools" \
  --repo orchard,example/orchard,stage,main \
  --local-root notes=/path/to/project-notes \
  --runtime codex \
  --runtime generic \
  --runtime claude-code \
  --schedule both \
  --hour 7 \
  --minute 0
```

Repository values use `ID,OWNER/REPOSITORY,STAGE,PRODUCTION`; local roots use `ID=PATH`. `--local-pattern` can be repeated and defaults to `**/*.md`. The installer refuses an initialized directory.

Schedule definitions appear under `generated/schedules/launchd/` and `generated/schedules/systemd/`. They capture absolute paths to the current Python interpreter, CLI checkout, and private data directory. Inspect them before copying or enabling them; regenerate after moving Python, the checkout, or the data directory. The installer does not copy, load, enable, or start a schedule.

On macOS, activation is a deliberate administrator/user step: copy the reviewed plist into `~/Library/LaunchAgents/`, then use `launchctl bootstrap` for the current GUI domain. On a systemd user session, copy the reviewed service and timer into `~/.config/systemd/user/`, run `systemctl --user daemon-reload`, then enable the generated timer. These commands change machine state and are intentionally not run by HFLedger.

## Security boundary

- The data directory is private state; do not commit it to the public engine repository.
- Schedules only invoke the collector. They do not invoke work, reconciliation, merge, or deployment prompts.
- Collector health is fail-loud. Disabled sources are shown as disabled, not silently healthy.
- Missing collector coverage is shown as unobserved, not quiet, verified, or
  all clear.
- Public and private adapter data stay separate; a collector or adapter grants
  no task, merge, deploy, send, or configuration authority.
- Stage merging requires both standing config and explicit authority in the current invocation.
- Production writes are rejected by Phase 3 configuration validation.
- See [`discipline.md`](discipline.md) for capture, completion, and pre-serve verification rules.
