# Owner-model implementation notes

This log records the implementation choices and verification for the owner
model defined in [`owner-model.md`](owner-model.md).

## Design decisions

- Keep `type: decision|action` as the existing authorization and resolution
  mechanism; add `cardKind` for the product judgment being requested.
- Keep legacy admitted asks valid. Typed validation applies when `cardKind` is
  present; Today projects untyped legacy asks into the closest owner zone
  without rewriting them.
- Accept the previous generated-count shape when only `cardKinds` is absent;
  all older counts must still match exactly, and the next board write adds the
  new grouping.
- Keep product evidence (`evidenceLinks`) distinct from technical drill-down
  (`footnoteLinks`).
- Treat a priority review as one admitted decision with a bounded ordered build
  list. Its resolution records both the surviving order and killed ids.
- Derive Today summary data on the server from the validated board and admitted
  cards; the browser only renders that closed projection.

## Deviations log

- None.

## Verification log

- `python3 tests/run_all.py`: 296 tests passed.
- `./scripts/release-check --allow-dirty`: release ready; board and ledger
  validation passed, the disposable demo exposed five cards and resolved one,
  and the external privacy gate reported its expected absence.
- Previous-release upgrade proof: removed the new production-health and
  `cardKinds` fields from a disposable board, then confirmed `ledger validate`
  and `ledger reconcile` exited successfully and the server returned Today with
  five card groups and the complete five-stage product flow.
- Browser review at desktop and narrow widths: production health led Today,
  all five card counts were legible, test-site failure remained neutral, and
  the deck presented product descriptions before technical footnotes.
- Browser console review: no warnings or errors.
