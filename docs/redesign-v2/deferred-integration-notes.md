# Implementation notes — HFLedger deferred integration (2026-07-19)

## Assumptions

- The immutable base is accepted V1 commit
  `ea00fc1801f3e426c92ccb4e02d8644edd75558f`.
- The selected product capabilities are exactly Prompt 21A Text Size, Prompt 16
  Quick Look, and Prompt 18 Deterministic Disputes.
- Prompts 14, 15, 17, 19, and 20 remain isolated and are not integration
  inputs.
- Remediation restores the existing read-only and authority contracts. It may
  add bounded read/navigation resolution, but no new authoritative writer.
- No push, publication, notarization, updater delivery, collector activation,
  or release is authorized.

## Decisions

- Remove active Decision Deck undo instead of adding a new event/writer path.
  Legacy `deck_undo` records remain readable for compatibility.
- Enforce read-only policy for the selected context instead of copying the
  primary context policy across the service.
- Route source opening through an exact projected-link resolver. Projected
  targets remain data and are never executed directly by the client.
- Remove unmodified native item accelerators that bypass editable/modal page
  guards instead of inventing a second native eligibility protocol.
- Integrate capabilities one at a time and rerun the focused boundary gate
  after each commit.

## Deviations

- Prompt 21A completed after the first Prompt 22 review and was added to the
  selected minimal bundle by the product owner's explicit follow-up authorization.
- Prompt 21A's `verify_release.py` artifact mutation will not be retained;
  verification must observe rather than repair the bundle.
- The Prompt 21 source resolver initially accepted legacy numeric IPv4 forms
  that browsers normalize into private addresses. The integration rejects the
  entire legacy numeric-host grammar and retains a client-side check.
- Prompt 18's isolated implementation compared every evidence pair before the
  projection cap. A 2,000-record fictional no-dispute case took about 17
  seconds, violating the six-second loop. The accepted implementation uses
  typed indexes and direct explicit-edge traversal; the 4,000-record regression
  completes in about 0.4 seconds.
- Prompt 18 keeps exact totals and complete affected-item classification beyond
  the 500-dossier cap. Reciprocal witness pairs are pinned within the global
  4,000-evidence cap, with an explicit omission flag only when the witness set
  itself cannot fit.
- Prompt 16 source buttons were ported from raw projection-target checks to the
  server-owned resolver. The fictional browser fixture now exposes the same
  read-only resolver response.

## Owner decisions recorded

- The private decision record selected the minimal path plus Prompt 21A and
  authorized bounded no-write-back remediation.
