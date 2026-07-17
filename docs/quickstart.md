# Quickstart

This guide reaches a real decision swipe in about two minutes, then creates a clean personal workspace and files an ask through the protocol.

## 1. Swipe the disposable demo

From a clone of HFLedger:

```sh
DEMO_HOME="$(mktemp -d)"
./scripts/ledger-demo "$DEMO_HOME"
./cli/ledger --home "$DEMO_HOME" serve
```

Open [http://127.0.0.1:7171/deck](http://127.0.0.1:7171/deck). The fictional Ovenlight bakery card offers two timer-alert choices and one recommendation. Tap either choice, tap “Accept recommendation,” or swipe the card right. The event and resolved card land in the disposable data directory, not in the engine checkout.

Open [http://127.0.0.1:7171/](http://127.0.0.1:7171/) to see the queue and resolved outcome. Stop the server with `Ctrl-C`.

## 2. Initialize a private workspace

Choose a directory outside the clone:

```sh
export LEDGER_HOME="$HOME/.ledger"
./cli/ledger init "$LEDGER_HOME" --project "My project"
./cli/ledger validate
```

The initializer refuses existing data files. `--home` overrides `LEDGER_HOME`; otherwise HFLedger uses `$XDG_DATA_HOME/ledger` and then `~/.ledger`.

## 3. File a decision

An admitted decision names the blocked outcome, genuine options, recommendation, risk, reversibility, rollback, and analysis already completed:

```sh
./cli/ledger ask decision \
  --key release:timer-mode \
  --title "Choose the timer default" \
  --blocks task:timer:release \
  --gate judgment \
  --human-reason "Two valid product behaviors remain after agent analysis." \
  --blocked-outcome "The timer release cannot proceed until one behavior is selected." \
  --risk "The wrong default could confuse users during a busy shift." \
  --risk-level medium \
  --reversibility reversible \
  --rollback "Restore the previous default and release a patch." \
  --work-done "Both modes were prototyped and checked against the requested scope." \
  --source "fictional release review" \
  --priority P1 \
  --question "Which timer behavior should new batches use?" \
  --option manual "Manual start" "Predictable but requires an explicit start" \
  --option automatic "Automatic start" "Faster but depends on accurate batch state" \
  --recommend manual \
  --recommend-why "Manual start is clearer for the first release and easy to revise."
```

The command appends one event; it does not edit the board. Fold and verify it:

```sh
./cli/ledger reconcile
./cli/ledger validate
./cli/ledger serve
```

The decision now appears on `/deck`. Reusing the same stable key is idempotent while open and rejected after resolution; use a new key only for a materially new decision.

## 4. Capture “already done” or “skip it”

Use the id printed by `ledger ask`:

```sh
./cli/ledger done \
  --id ASK_ID \
  --evidence "The owner confirmed the selected timer behavior is complete." \
  --source "release review"
./cli/ledger reconcile
./cli/ledger validate
```

Use `ledger skip` for an intentional skip. If no board id exists, target the exact stable key with `--key`. Unmatched completions are escrowed rather than lost.

## 5. Give an agent the operating contract

New workspaces default to the generic instruction layout:

```sh
./cli/ledger render-packs
```

Review files under `$LEDGER_HOME/generated/packs/`. Copy or reference them deliberately from the agent runtime; HFLedger does not alter global prompt directories. To configure Claude Code, collectors, repositories, and inactive schedules, continue with [`automation.md`](automation.md).

## Next reading

- [`protocol.md`](protocol.md): formats, invariants, lifecycle, and exit statuses.
- [`discipline.md`](discipline.md): what belongs in queue, inbox, or the owner lane.
- [`ui.md`](ui.md): local board/deck API and security boundary.
- [`automation.md`](automation.md): packs, collectors, installer, and schedules.
