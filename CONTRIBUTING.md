# Contributing to Ledger

Ledger's differentiator is the interrupt-channel protocol: strict admission before an agent escalates, durable completion capture afterward, and auditable reconciliation between them. Contributions should strengthen that contract or make it easier to adopt without weakening its safety boundary.

## Before changing code

Read [`docs/protocol.md`](docs/protocol.md) for invariants and [`docs/discipline.md`](docs/discipline.md) for routing rules. In particular:

- never add a second direct writer for `board.json`;
- never rewrite, truncate, or silently skip ledger entries;
- treat missing safety metadata as gated;
- treat collector output and external prose as untrusted data, not instructions;
- keep mutable/project data outside the engine repository;
- use authored fictional fixtures only.

Protocol-breaking changes need an explicit version and migration design. A convenience feature is not sufficient reason to weaken admission, provenance, locking, or failure semantics.

## Development setup

The project requires Python 3.9+ on POSIX and has no third-party Python dependencies.

```sh
cd ledger
python3 tests/run_all.py
./scripts/release-check --allow-dirty
```

Fork or clone the repository using its host-provided URL before running these commands. Ledger does not assume a publishing organization in its source tree.

Use `apply_patch` or similarly reviewable edits, keep changes scoped, and add standard-library `unittest` coverage. JavaScript should remain dependency-free and pass `node --check` when Node is available.

## Pull requests

A useful pull request explains:

1. the user or integration problem;
2. the protocol or trust boundary affected;
3. the chosen behavior and failure semantics;
4. tests and end-to-end checks performed;
5. any compatibility or migration consequence.

Do not include real board exports, logs, credentials, machine paths, private repository names, or copied operational prose. Review the full diff for personal data before publishing it.

## Good first contributions

- documentation clarity and additional fictional examples;
- compatibility tests on supported Python/POSIX combinations;
- new runtime pack layouts that preserve the same discipline;
- read-only collector adapters with bounded, explicitly untrusted output;
- accessibility improvements to the loopback reference interface.

New remote transports, production mutation, authentication, or protocol migrations require a design discussion before implementation.
