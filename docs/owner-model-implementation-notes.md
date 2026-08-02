# Owner-model implementation notes

This log records the implementation choices and verification for the owner
model defined in [`owner-model.md`](owner-model.md).

## Design decisions

- Keep `type: decision|action` as the existing authorization and resolution
  mechanism; add `cardKind` for the product judgment being requested.
- Keep legacy admitted asks valid. Typed validation applies when `cardKind` is
  present; Today projects untyped legacy asks into the closest owner zone
  without rewriting them.
- Keep product evidence (`evidenceLinks`) distinct from technical drill-down
  (`footnoteLinks`).
- Treat a priority review as one admitted decision with a bounded ordered build
  list. Its resolution records both the surviving order and killed ids.
- Derive Today summary data on the server from the validated board and admitted
  cards; the browser only renders that closed projection.

## Deviations log

- None.

## Verification log

- `python3 tests/run_all.py`: 290 tests passed.
- `./scripts/release-check --allow-dirty`: release ready; board and ledger
  validation passed, the disposable demo exposed five cards and resolved one,
  and the external privacy gate reported its expected absence.
- Browser review at desktop and narrow widths: production health led Today,
  all five card counts were legible, test-site failure remained neutral, and
  the deck presented product descriptions before technical footnotes.
- Browser console review: no warnings or errors.
