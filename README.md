# Ledger

Ledger is a local-first protocol and core engine for governing the interrupt channel between AI agents and the person who runs them. It gives agents an append-only output log, admits only well-formed decisions and owner-only manual actions into the human lane, captures completion reports durably, and reconciles those events into one validated JSON board.

This repository is Phase 1: the protocol, standard-library Python engine, CLI, fictional example, and tests. It intentionally has no web interface, runtime-specific agent pack, background collector, server, SDK, or package installer yet.

## Requirements and safety model

- Python 3.9 or newer and Git; no third-party Python packages.
- POSIX file locking through `fcntl`; Phase 1 does not support native Windows writers.
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

The fictional [`example/`](example/) directory is a valid data directory and can be checked with:

```sh
LEDGER_HOME="$PWD/example" ./cli/ledger validate
```

## Development

```sh
python3 tests/run_all.py
```

The formal integration contract is [`docs/protocol.md`](docs/protocol.md). License: MIT.
