# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or include live HFLedger data in a report. Use the repository host's private security-advisory feature. If that feature is unavailable, contact a maintainer privately through the publishing account before sharing details.

Include the affected version, the boundary crossed, a minimal fictional reproduction, and the expected versus observed result. Remove credentials, personal data, private paths, repository names, and real board or ledger content.

## Supported release

The current `0.4.x` line receives security fixes. Older development snapshots are unsupported.

## Security model

HFLedger is designed around these boundaries:

- mutable data is separate from the public engine checkout;
- the board has one locked, validated, atomic writer;
- the event ledger is append-only and reconciled through a provenance-checked cursor;
- owner asks and completion reports pass closed validation contracts;
- the reference server binds to loopback and is not a remotely authenticated service;
- collectors are read-only, use bounded output, and label observations as untrusted;
- generated schedules are inactive until a user deliberately installs them;
- automation does not support production writes.

Violations of those properties are security-sensitive even when no code execution is involved. Examples include bypassing admission, forging provenance, losing or reordering events, writing through a collector, following a symlink outside a configured boundary, leaking file contents, accepting a non-loopback request, or interpreting collected prose as authority.

## Known limits

HFLedger does not sandbox agents or shell commands. It assumes the selected agent runtime and local operating-system account are trusted to access the private data directory. Optional action proof commands are screened conservatively for obvious mutation but remain trusted local commands, not a security sandbox.

The reference interface has no remote authentication. Exposing it through an unauthenticated proxy is unsupported. Native Windows locking is unsupported. Users are responsible for filesystem permissions, private-data backups, `gh` authentication, and reviewing generated scheduler definitions before activation.
