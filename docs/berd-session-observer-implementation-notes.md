# Berd session observer implementation notes

## Scope delivered

- Optional read-only collection through Berd's bundled CLI.
- Zero-message metadata reads with closed normalization and bounded output.
- Independent private freshness report and Operations projection.
- Compact Today counts plus product-led Operations rows.
- Exact task association only; no conversation-title or path inference.
- One owner-facing refresh action that uses the existing reconciler and
  collector rather than a parallel observer path.

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
4. Current Berd releases require the CLI to inherit a Berd-started control-file
   environment. The public observer does not discover private control files or
   bypass that boundary; missing environment degrades the source safely.
5. Native startup verifies the engine and workspace identity through a bounded
   readiness response. It does not repeatedly build the full owner projection
   while a large valid workspace is opening.
6. Automatic file-watch reloads remain presentation-only. The explicit
   **Refresh now** action has its own allowlisted request so report-file changes
   cannot recursively start more scans.

## Deferred

- Additional harness adapters may extend the same normalized owner projection
  behind a new closed report version later.
- A sanctioned deep link back to a Berd session can be added when both products
  expose and document a stable safe-link contract.
- Self-contained external polling remains deferred until Berd exposes a stable,
  vendor-supported discovery contract. Scheduling and environment handoff stay
  installation choices; this change does not install, enable, or repair them.
