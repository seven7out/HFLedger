# Berd session observer implementation notes

## Scope delivered

- Optional read-only collection through Berd's bundled CLI.
- Zero-message metadata reads with closed normalization and bounded output.
- Independent private freshness report and Operations projection.
- Compact Today counts plus product-led Operations rows.
- Exact task association only; no conversation-title or path inference.

## Deviations

1. The initial spike proposed adding sessions directly to the existing
   commands-and-schedules report. The implementation uses a separate closed
   session report instead. Session polling and recurring-job reporting have
   different cadences and writers; separating them prevents one source from
   refreshing or racing the other while still presenting one Operations view.
2. Raw session titles were considered as secondary detail but are not retained.
   They can contain prompts, implementation language, or sensitive project
   context. Exact task links provide an owner-facing headline; unlinked work
   receives a generic label.
3. The observer does not infer that `waiting` needs the owner or that `stopped`
   means done. Those claims require HFLedger's existing admission and completion
   evidence rather than runtime metadata.

## Deferred

- Additional harness adapters may extend the same normalized owner projection
  behind a new closed report version later.
- A sanctioned deep link back to a Berd session can be added when both products
  expose and document a stable safe-link contract.
- Automated polling remains an installation choice; this change does not
  install, enable, or repair a schedule.
