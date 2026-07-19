# Prompt 18 implementation notes

## Blindspot pass

- **Repository and territory:** public HFLedger repository, isolated branch
  `deferred/redesign-v2-disputes`, exact accepted V1 base
  `ea00fc1801f3e426c92ccb4e02d8644edd75558f`.
- **Prior work reviewed before implementation:** HFLC operating contract,
  Prompt 18, Ready-for-Build admission task
  `task-hfledger-redesign-v2-disputes`, canonical redesign contract, accepted
  dogfood report, repository status, branches, and worktrees.
- **Hard constraints:** generic public engine; exact stable identities and typed
  references only; deterministic output; bounded untrusted text; no numeric
  confidence; no model or fuzzy matching; absence and disabled collectors are
  never disputes; no authoritative mutation or source arbitration; fictional
  fixtures only.
- **Architecture-changing unknowns to resolve from the repository:** where the
  detector belongs in the projection pipeline; which normalized observation
  shapes already exist; how current `disputed` home classification consumes
  evidence; whether Prompt 18 can remain an isolated module or requires a
  versioned projection-shape change.
- **Detail-level unknowns:** exact terminal-status vocabulary, check/PR state
  vocabulary, timestamp normalization, duplicate canonicalization, reason and
  claim text caps, and the smallest focused test surface.
- **Conservative default:** add a pure closed-schema detector and integrate it
  only through existing typed evidence/adapter seams. Do not expand public
  authority, collector capabilities, or projection fields unless the accepted
  contract already provides a compatible location.

No owner interview is needed at this point: Prompt 18 supplies the product
boundary, and the remaining unknowns are discoverable implementation details.

## Deviations log

- The repository guide references
  `.claude/skills/finding-unknowns/SKILL.md`, but no such file exists under the
  repository launch root or the user's `.claude` tree. The required blindspot,
  ambiguity, conservative-default, implementation-notes, and deviation steps
  are being applied directly from the repository guide.
- The accepted V1 contract deferred advanced dispute records and therefore had
  no top-level dispute collection. This branch adds backward-compatible
  `disputes`, `totals.disputes`, and dossier `disputeIds` fields. Existing V2
  clients ignore unknown fields; Prompt 22 remains the selection gate.
- Repository-wide workflow-run observations cannot be associated with an exact
  item under the current public schema. Required-CI disputes therefore consume
  only exact normalized `required-check` evidence from an adapter. No branch,
  title, or time-proximity association was invented.

## Open unknowns

- No implementation-blocking unknown remains.
- Product selection remains intentionally open until Prompt 22. The technical
  recommendation is to integrate this detector without adding collector
  permissions or a new navigation surface.
