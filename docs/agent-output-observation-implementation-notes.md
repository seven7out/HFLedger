# Agent output observation implementation notes

## Scope delivered

- One bounded latest output on a validated Operations job.
- Closed output kinds for candidate research, reports, evidence, and other
  product-shaped results.
- Exact optional task association with an **Open related work** action.
- Plain-language validation and opaque path-free output references.
- Fictional public fixtures and compatibility with version-1 and version-2
  Operations reports.

## Deviations

1. The initial direction mentioned linking directly to a sanitized packet. The
   public web client does not gain local-file authority. It exposes an opaque
   reference and opens the exact related HFLedger task instead. An installation
   may project a separately sanctioned public web link through the existing link
   resolver, but this report cannot introduce one.
2. The implementation does not ingest full output history. The latest artifact
   is enough to orient the owner; durable history remains in the authoritative
   evidence or research system.
3. Agent output stays in Operations instead of creating owner cards. Only the
   existing five owner judgment zones may interrupt the owner.
4. The report does not infer task association from titles, prompts, paths, or
   conversation text. The installation must provide an exact validated task id.
5. Building the Mac app exposed a stale Python 3.9 license lookup. The native
   bundler now asks its isolated build interpreter for the standard-library and
   package locations, so the documented build works with the supported Python
   runtime without changing the app's runtime contract.

## Deferred

- A separate bounded artifact catalog if an installation needs more than the
  latest output.
- A sanctioned local-file opening capability with its own allowlist and native
  security review.
