# Implementation notes — Operations drill-down (2026-08-26)

## Assumptions

- Operations rows should reuse the existing Details inspector and selection
  model instead of opening a separate modal or page.
- Product purpose, the latest reported outcome, and safe owner action are
  primary. Runner metadata, identifiers, and output references are secondary.
- A problematic status does not prove a root cause or create owner authority to
  rerun work. The interface may explain the bounded observation and suggest a
  conservative investigation step only.
- Existing closed Operations reports already contain enough information for the
  first drill-down; no report-schema expansion is required unless tests reveal
  otherwise.

## Decisions

- Add dedicated schedule and session descriptors to the existing visible-row
  registry and Details dispatcher.
- Make the whole row keyboard-accessible while preserving nested related-work
  and bounded-output controls as independent actions.
- Derive status-specific owner guidance entirely from validated state already in
  the projection; never parse or display logs, paths, prompts, or conversations.

## Deviations

- The initial issue looked like a missing explanation for one failed job.
  Inspection showed the deeper gap is that neither recurring-job nor agent-
  session rows participate in HFLedger's selection model. The repair therefore
  covers both row types and their inspector views.
