# Berd session observation

HFLedger can optionally show the current shape of agent work hosted by Berd in
**Today** and **Operations**. The integration is deliberately an observer, not
a conversation importer or agent controller.

The owner sees four useful states: working, waiting, stopped, and problem. A
stale observation becomes unknown. These are process observations only:

- idle or stopped does not mean the product outcome is complete;
- waiting does not automatically mean the owner is blocking the work;
- a problem does not automatically file a `stuck_alarm`;
- no session state changes task priority, task status, or authority.

## Private configuration

Add the optional `automation.sources.berd` object to the private workspace
configuration:

```json
{
  "enabled": true,
  "executable": "berdctl",
  "sessionLimit": 20,
  "staleAfterSeconds": 300,
  "sessionTasks": {
    "fictional-session-1": "task-example"
  }
}
```

`executable` is either `berdctl`, resolved by the operating system, or an
absolute path ending in `berdctl` selected by the installation. `sessionTasks` is an
optional exact-id map. It is the only way the public adapter links a session to
a product task; title similarity, folder names, prompts, and prose are never
used for association. Existing configurations without the Berd object remain
valid and treat this source as disabled.

Run one attended refresh with:

```sh
ledger --home /path/to/private-ledger-data collect
```

Berd must be open and its bundled CLI must be available. A missing app, missing
CLI, timeout, non-zero exit, malformed response, or partially unreadable list
is reported as degraded rather than healthy.

## Metadata boundary

The adapter calls only the read-only metadata surface:

```sh
berdctl session list --limit 20 --json
berdctl session get --session-id fictional-session-1 --messages 0 --json
```

It retains a bounded session id, normalized state, creation and update times,
optional exact task id, and secondary harness/model/agent labels. It drops
session titles, project ids, working directories, message counts, message
payloads, and every unsupported field. It never reads Berd's database or local
conversation files directly.

The normalized result is atomically written as the private mode-`0600`
`reports/session-observer-latest.json` file. Its freshness is independent of
`reports/operations-latest.json`, so a current session poll cannot make an old
recurring-job report appear current. The server watches both reports and merges
their closed projections into Operations.

## Owner presentation

Today shows one compact **Agents now** line with working, waiting, stopped, and
problem counts. Operations leads each linked row with the task's owner-facing
product headline. Harness, model, agent identity, timestamps, and the opaque
source reference remain secondary details. Unlinked sessions use the neutral
headline **Unlinked agent session** rather than exposing a raw conversation
title.

The adapter and version-1 report are Berd-specific today. Their small normalized
shape gives a future read-only harness adapter a clear extension point without
changing the owner model.
