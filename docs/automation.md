# Instruction packs, collectors, and installation

Phase 3 adds a read-only observation layer and generated operating instructions around the HFLedger protocol. Collectors never edit the board or append events. Packs explain how an agent should interpret observations and use existing HFLedger commands; they are not executable policy engines.

## Automation configuration

The optional `automation` object in `config.json` has this closed shape:

| Field | Meaning |
| --- | --- |
| `ownerRole` | Neutral role name inserted into generated instructions. |
| `repositories[]` | Repository id, GitHub `owner/repository` slug, stage branch, and production branch. |
| `sources.github` | Enable switch and bounded limits for open PRs, merged PRs, runs, and issues. |
| `sources.localFiles.roots[]` | Local root id, path, relative glob patterns, and per-root file limit. |
| `workPolicy` | Ready/review statuses, stage-merge eligibility, and the required `productionWrites: false`. |
| `packs.runtimes` | Unique selection of `generic` and `claude-code`. |
| `schedule` | Whether a generated schedule is intended and its local hour/minute. |

Existing data directories without `automation` still validate, but `collect` and `render-packs` require the object. Copy the neutral block from [`config.example.json`](../config.example.json), edit it, and run `ledger validate` before generating artifacts.

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

### GitHub

The GitHub adapter calls an already authenticated `gh` executable with argument arrays, never a shell. It reads bounded lists of pull requests, workflow runs, and issues plus a production-to-stage branch comparison. It does not fetch comments, bodies, secrets, repository contents, or write endpoints.

Titles and workflow names become bounded single-line fields prefixed with `[untrusted]`. Other external strings are control-stripped and length-bounded. Counts and identifiers remain typed. A failure in one configured repository degrades the GitHub source even when other repository observations succeed.

Authenticate and inspect access separately with `gh auth status`. HFLedger does not store a GitHub token.

### Local files

The local adapter walks configured roots without following directory or file symlinks. It matches relative globs and records only the root id, a digest of the relative path, a bounded display summary of that path, extension, byte size, and modification time. It never opens or reads file contents. Missing or unreadable roots degrade the source; reaching `maxFiles` is reported through `truncatedRoots`.

Collector reports are observations. An agent must not treat a title, path, status, or report change as authority to execute work, create an owner ask, merge, or deploy.

## Instruction packs

Render the configured runtimes into the private data directory:

```sh
ledger --home /path/to/private-ledger-data render-packs
```

The generic adapter produces `AGENTS.md` plus `prompts/{sweep,work,attend,status}.md`. The Claude Code adapter produces `CLAUDE.md` plus `.claude/commands/ledger-{sweep,work,attend,status}.md`. A deterministic `manifest.json` records every relative path and SHA-256 digest.

Rendering is strict: templates may use only declared placeholders, unresolved tokens fail, and symlink targets are rejected. Existing generated files are not replaced unless `--force` is supplied. Generation does not copy files into a repository or a runtime-global configuration directory.

## Fresh installation

The Phase 3 installer creates a new data directory, validates and writes configuration, renders packs, and optionally generates inactive scheduler definitions:

```sh
./install/ledger-install /path/to/private-ledger-data \
  --project "Fictional orchard tools" \
  --repo orchard,example/orchard,stage,main \
  --local-root notes=/path/to/project-notes \
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
- Stage merging requires both standing config and explicit authority in the current invocation.
- Production writes are rejected by Phase 3 configuration validation.
- See [`discipline.md`](discipline.md) for capture, completion, and pre-serve verification rules.
