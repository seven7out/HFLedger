"""Append-only private observation store for the shadow history adapter.

The store is the adapter's only write surface.  It lives in a private
directory named by the runtime settings document — never inside the
repository, never inside the served application's data files — and it holds
one JSON record per line: run markers and retained source observations.

Retention is append-only with a hard cap.  When the cap is reached the store
refuses new appends with an explicit error instead of silently truncating;
silent truncation would turn missing observation into false completeness.
"""

from __future__ import annotations

import fcntl
import json
import os


STORE_SCHEMA_VERSION = 1
STORE_FILE = "observations.jsonl"
LOCK_FILE = "store.lock"
MAX_STORE_RECORDS = 100_000
RECORD_TYPES = frozenset(("run-started", "source-observation", "run-completed"))


class HistoryStoreError(ValueError):
    """The private store is unusable; the adapter must fail closed."""


def store_path(store_dir):
    return os.path.join(store_dir, STORE_FILE)


def _lock_path(store_dir):
    return os.path.join(store_dir, LOCK_FILE)


def _validate_record(record, line_number):
    if not isinstance(record, dict):
        raise HistoryStoreError(
            "store line %d is not an object" % line_number)
    if record.get("storeSchemaVersion") != STORE_SCHEMA_VERSION:
        raise HistoryStoreError(
            "store line %d has an unsupported schema version" % line_number)
    if record.get("type") not in RECORD_TYPES:
        raise HistoryStoreError(
            "store line %d has an unsupported record type" % line_number)
    run_seq = record.get("runSeq")
    if not isinstance(run_seq, int) or isinstance(run_seq, bool) or run_seq < 1:
        raise HistoryStoreError(
            "store line %d has an invalid run sequence" % line_number)
    return record


def read_all(store_dir):
    """Read and structurally validate every retained store record.

    A malformed private store fails closed: the adapter refuses to generate
    an envelope from observation history it cannot trust, rather than
    guessing which records to keep.
    """
    path = store_path(store_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise HistoryStoreError("store is unreadable: %s" % exc)
    records = []
    last_seq = 0
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            raise HistoryStoreError("store line %d is blank" % line_number)
        try:
            record = json.loads(raw)
        except ValueError as exc:
            raise HistoryStoreError(
                "store line %d is malformed: %s" % (line_number, exc))
        record = _validate_record(record, line_number)
        if record["runSeq"] < last_seq:
            raise HistoryStoreError(
                "store line %d breaks run-sequence monotonicity" % line_number)
        last_seq = record["runSeq"]
        records.append(record)
    return records


def next_run_seq(records):
    return max((record["runSeq"] for record in records), default=0) + 1


def append(store_dir, records):
    """Durably append records under an exclusive lock; never rewrite."""
    if not records:
        return
    for record in records:
        _validate_record(record, 0)
    os.makedirs(store_dir, mode=0o700, exist_ok=True)
    existing = read_all(store_dir)
    if len(existing) + len(records) > MAX_STORE_RECORDS:
        raise HistoryStoreError(
            "store cap of %d records reached; refusing to append (no silent truncation)"
            % MAX_STORE_RECORDS)
    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True) + "\n"
        for record in records
    ).encode("utf-8")
    with open(_lock_path(store_dir), "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            fd = os.open(store_path(store_dir),
                         os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                offset = 0
                while offset < len(payload):
                    written = os.write(fd, payload[offset:])
                    if written <= 0:
                        raise HistoryStoreError("short store write")
                    offset += written
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
