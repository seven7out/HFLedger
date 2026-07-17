# Ledger

Ledger is a local-first protocol and core engine for governing the interrupt channel between AI agents and the person who runs them. It gives agents an append-only output log, admits only well-formed decisions and owner-only manual actions into the human lane, captures completion reports durably, and reconciles those events into one validated JSON board.

The repository now includes the Phase 1 protocol engine, the Phase 2 reference interface, and the Phase 3 automation boundary: generic and Claude Code instruction packs, read-only GitHub and local-file collectors, and an installer that generates inactive launchd/systemd schedules. Remote serving, additional runtime/source adapters, an SDK, and package distribution remain future work.

## Requirements and safety model

- Python 3.9 or newer and Git; no third-party Python packages.
- The authenticated `gh` CLI is optional and required only when the GitHub collector is enabled.
- POSIX file locking through `fcntl`; the current engine does not support native Windows writers.
- The engine repository never contains live state. Each installation uses a separate data directory.
- Board writes use one locked load/mutate/validate/atomic-replace path with backups.
- Ledger writes use `O_APPEND`, an exclusive file lock, and `fsync`.
- This project shares a name with the ledger-cli accounting program. If both are installed, use a shell alias such as `alias agent-ledger=/path/to/ledger/cli/ledger`.

## Quickstart

Clone the repository, then run the entrypoint directly:

```sh
git clone <repository-url> ledger
cd ledger
./cli/ledger init /tmp/ovenlight --project "Ovenlight Bakery Tools"
export LEDGER_HOME=/tmp/ovenlight
./cli/ledger validate
```

File a decision with two options:

```sh
./cli/ledger ask decision \
  --key release:timer-mode \
  --title "Choose the proofing timer mode" \
  --blocks task:timer:release \
  --gate judgment \
  --human-reason "Two valid product behaviors remain after agent analysis." \
  --blocked-outcome "The timer release cannot proceed until one behavior is selected." \
  --risk "The wrong default could confuse bakers during a busy shift." \
  --risk-level medium \
  --reversibility reversible \
  --rollback "Restore the previous timer default and release a patch." \
  --work-done "Both modes were implemented in a local prototype and reviewed." \
  --source "fictional release planning session" \
  --priority P1 \
  --question "Which timer behavior should be the default for new batches?" \
  --option manual "Manual start" "Predictable but requires a baker to start every timer" \
  --option automatic "Automatic start" "Faster but depends on accurate batch status updates" \
  --recommend manual \
  --recommend-why "Manual start is clearer for the first release and easy to revise."
```

The command appends an event; it does not edit the board. Fold it and inspect the result:

```sh
./cli/ledger reconcile
./cli/ledger validate
python3 -m json.tool "$LEDGER_HOME/board.json"
```

Open the local board and decision deck:

```sh
./cli/ledger serve
```

Visit `http://127.0.0.1:7171/` for the board or `http://127.0.0.1:7171/deck` for the mobile deck. The service binds only to loopback. Decisions, snoozes, and action completions use the same registered event and reconciliation paths as the CLI; the interface does not write around the protocol.

Capture a completion report, then reconcile again:

```sh
./cli/ledger done \
  --id ask-bdec785b342795e8 \
  --evidence "The owner confirmed the selected timer mode is complete." \
  --source "release review"
./cli/ledger reconcile
```

Run `./cli/ledger --help` and each subcommand's `--help` for all flags. The deterministic ask id shown above is derived from the stable key; use the id printed by your own command.

## Data directory

`ledger init [DIR]` creates:

```text
config.json
board.json
ledger.jsonl
locks/
backups/
reports/
```

Path resolution is explicit `--home`, then `LEDGER_HOME`, then `$XDG_DATA_HOME/ledger`, then `~/.ledger`. To synchronize state across trusted machines, initialize a separate private Git repository inside the data directory. Commit only after writers are stopped, pull before work, and never publish a data repository that may contain operational or private text.

The optional `ui` object in `config.json` controls the interface title, subtitle, six-digit accent color, port, and an allowlist of contexts. Each context names an independent initialized Ledger data directory. Relative context homes are resolved from the primary data directory; HTTP requests select only configured context ids and cannot supply paths. See [`docs/ui.md`](docs/ui.md) for the complete configuration and API contract.

For a fresh automation-ready directory, the Phase 3 installer can configure sources, render agent packs, and generate schedules in one step:

```sh
./install/ledger-install /tmp/ovenlight-automation \
  --project "Ovenlight Bakery Tools" \
  --runtime generic \
  --runtime claude-code
```

Generated assets remain under the private data directory. The installer does not copy prompts into a project or activate OS schedules. Configure optional sources, collector behavior, layouts, and deliberate schedule activation using [`docs/automation.md`](docs/automation.md). The runtime-neutral routing and verification contract is in [`docs/discipline.md`](docs/discipline.md).

The fictional [`example/`](example/) directory is a valid data directory and can be checked with:

```sh
LEDGER_HOME="$PWD/example" ./cli/ledger validate
```

## Development

```sh
python3 tests/run_all.py
```

The formal engine contract is [`docs/protocol.md`](docs/protocol.md); the reference-interface contract is [`docs/ui.md`](docs/ui.md); automation is specified in [`docs/automation.md`](docs/automation.md). License: MIT.
