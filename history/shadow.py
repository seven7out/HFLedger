"""Standalone shadow-run harness: ``python3 -m history.shadow --settings <path>``.

The harness is the only place the history package performs I/O:

- it READS the workspace ledger (through the protocol's shared-lock snapshot,
  whose only side effect is the workspace's own ``locks/ledger.lock``
  coordination file), the board document, declared mirror files, and the
  workspace configuration's collector-source switches;
- it WRITES only inside the private store directory named by the settings
  document: the append-only observation store, ``envelope-latest.json``, and
  ``report-latest.md``.

It never writes ``board.json``, never appends to any ledger, never executes
Git or any subprocess, never opens a network connection, and never mutates
workspace configuration.  The gate is the settings document itself: without
``"historyAdapterV1": true`` the harness exits without observing anything,
which leaves current application behavior untouched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
from zoneinfo import ZoneInfo

from core import ledger as core_ledger

from . import adapter
from . import store as history_store


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(moment):
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chain_digest(lines, upto):
    """Raw-byte hash chain over the first ``upto`` lines.

    Any byte change anywhere in that prefix — not just the newest line —
    changes the chain, so a later run can prove the previously observed
    prefix is still exactly what it observed.
    """
    digest = b""
    for raw in lines[:upto]:
        digest = hashlib.sha256(digest + raw.encode("utf-8")).digest()
    return digest.hex()


def _observation(run_seq, scope, started, completed, result, count,
                 cursor_start=None, cursor_end=None, extra=None):
    record = {
        "storeSchemaVersion": history_store.STORE_SCHEMA_VERSION,
        "type": "source-observation",
        "runSeq": run_seq,
        "obsId": "obs-%06d-%s" % (run_seq, scope),
        "sourceKey": scope,
        "attemptStartedAt": _iso(started),
        "attemptCompletedAt": _iso(completed),
        "result": result,
        "recordCount": count,
        "truncated": False,
        "cursorStart": cursor_start,
        "cursorEnd": cursor_end,
    }
    if extra:
        record.update(extra)
    return record


def observe_workspace(home, settings, prior_records, clock=_now):
    """Read every configured source once; return the new store records.

    Reads only.  The returned records are appended to the private store by
    the caller; nothing in the workspace is modified.
    """
    run_seq = history_store.next_run_seq(prior_records)
    observations = []
    facts = {"mirrors": {}, "ledgerLines": [], "board": None,
             "ledgerIntegrity": {"prefixVerified": True, "shrunk": False}}

    prior_cursor = None
    for record in prior_records:
        if (record.get("type") == "source-observation"
                and record.get("sourceKey") == "ledger"
                and isinstance(record.get("linesAfter"), int)):
            if prior_cursor is None or record["runSeq"] >= prior_cursor["runSeq"]:
                prior_cursor = {
                    "runSeq": record["runSeq"],
                    "linesAfter": record["linesAfter"],
                    "chainLine": record.get("chainLine"),
                    "chainDigest": record.get("chainDigest"),
                }

    # --- ledger ----------------------------------------------------------
    started = clock()
    try:
        lines = core_ledger.snapshot_lines(home)
        completed = clock()
        facts["ledgerLines"] = lines
        shrunk = bool(prior_cursor and len(lines) < prior_cursor["linesAfter"])
        prefix_verified = True
        if prior_cursor and isinstance(prior_cursor.get("chainLine"), int):
            chain_line = prior_cursor["chainLine"]
            if chain_line <= len(lines):
                prefix_verified = (
                    _chain_digest(lines, chain_line) == prior_cursor["chainDigest"])
            else:
                prefix_verified = False
        facts["ledgerIntegrity"] = {
            "prefixVerified": prefix_verified and not shrunk,
            "shrunk": shrunk,
        }
        observations.append(_observation(
            run_seq, "ledger", started, completed,
            "complete-nonempty" if lines else "complete-empty", len(lines),
            cursor_start="l%06d" % (prior_cursor["linesAfter"] if prior_cursor else 0),
            cursor_end="l%06d" % len(lines),
            extra={
                "linesAfter": len(lines),
                "chainLine": len(lines),
                "chainDigest": _chain_digest(lines, len(lines)),
            }))
    except OSError:
        observations.append(_observation(
            run_seq, "ledger", started, clock(), "failed", 0))

    # --- board -----------------------------------------------------------
    started = clock()
    board_path = os.path.join(home, "board.json")
    try:
        with open(board_path, "rb") as handle:
            raw = handle.read()
        completed = clock()
        try:
            board = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            observations.append(_observation(
                run_seq, "board", started, completed, "malformed", 0))
        else:
            facts["board"] = board if isinstance(board, dict) else None
            count = sum(
                len(board.get(section, []) or [])
                for section in ("queue", "inbox", "ownerTasks")
            ) if isinstance(board, dict) else 0
            observations.append(_observation(
                run_seq, "board", started, completed,
                "complete-nonempty" if count else "complete-empty", count,
                cursor_end="b%s" % hashlib.sha256(raw).hexdigest()[:16]))
    except OSError:
        observations.append(_observation(
            run_seq, "board", started, clock(), "failed", 0))

    # --- the store itself ------------------------------------------------
    started = clock()
    observations.append(_observation(
        run_seq, "store", started, clock(),
        "complete-nonempty" if prior_records else "complete-empty",
        len(prior_records)))

    # --- collector-source switches from workspace configuration ----------
    config_sources = {}
    try:
        with open(os.path.join(home, "config.json"), encoding="utf-8") as handle:
            config = json.load(handle)
        automation = config.get("automation") if isinstance(config, dict) else None
        if isinstance(automation, dict) and isinstance(automation.get("sources"), dict):
            config_sources = automation["sources"]
    except (OSError, ValueError):
        config_sources = {}
    for config_key, scope in (("github", "github"), ("localFiles", "local-files")):
        entry = config_sources.get(config_key)
        if isinstance(entry, dict) and entry.get("enabled") is False:
            started = clock()
            observations.append(_observation(
                run_seq, scope, started, clock(), "disabled", 0))
        # An enabled collector's report history belongs to a retained report
        # store this shadow adapter does not create; without retained
        # attempts the interval simply stays unknown.

    # --- declared mirrors ------------------------------------------------
    for mirror in settings.get("sources", {}).get("mirrors", []):
        scope = "mirror-%s" % mirror["id"]
        anchor = mirror.get("anchor", {})
        started = clock()
        path = os.path.join(home, mirror["fileName"])
        try:
            digest = _sha256_file(path)
            completed = clock()
            matches = digest == anchor.get("fileSha256")
            facts["mirrors"][scope] = {
                "anchoredAt": anchor.get("anchoredAt"),
                "shaMatches": matches,
            }
            observations.append(_observation(
                run_seq, scope, started, completed,
                "complete-nonempty" if matches else "malformed",
                anchor.get("lines", 0) if matches else 0,
                cursor_end="l%06d" % anchor.get("lines", 0) if matches else None))
        except OSError:
            facts["mirrors"][scope] = {
                "anchoredAt": anchor.get("anchoredAt"),
                "shaMatches": None,
            }
            observations.append(_observation(
                run_seq, scope, started, clock(), "failed", 0))

    run_started = {
        "storeSchemaVersion": history_store.STORE_SCHEMA_VERSION,
        "type": "run-started",
        "runSeq": run_seq,
        "runId": "run-%06d" % run_seq,
        "startedAt": observations[0]["attemptStartedAt"],
    }
    return run_seq, [run_started] + observations, facts


def _atomic_private_write(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def _qualified_weeks(envelope):
    """Complete local Monday-to-Monday weeks inside the final ledger window."""
    zone = ZoneInfo(envelope["calendar"]["timeZone"])
    final_windows = [
        window for window in envelope["coverageWindows"]
        if window["sourceId"] == "src-ledger" and window["finality"] == "final"
        and window["state"].startswith("complete-")
    ]
    if not final_windows:
        return []
    window = final_windows[0]
    start = dt.datetime.fromisoformat(window["from"].replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(window["through"].replace("Z", "+00:00"))
    local_start = start.astimezone(zone)
    days_ahead = (7 - local_start.weekday()) % 7
    if days_ahead == 0 and local_start.time() != dt.time(0):
        days_ahead = 7
    monday = local_start.date() + dt.timedelta(days=days_ahead)
    weeks = []
    while True:
        week_start = dt.datetime.combine(monday, dt.time(0), tzinfo=zone)
        week_end = dt.datetime.combine(
            monday + dt.timedelta(days=7), dt.time(0), tzinfo=zone)
        if week_end.astimezone(dt.timezone.utc) > end:
            break
        weeks.append((week_start.date().isoformat(), week_end.date().isoformat()))
        monday += dt.timedelta(days=7)
    return weeks


def _report_text(envelope, weeks):
    counts = {
        name: len(envelope[name])
        for name in ("sources", "sourceObservations", "coverageWindows", "items",
                     "lifecycleEvents", "verificationEvents", "blockerEpisodes",
                     "runs", "runOutcomes", "diagnostics")
    }
    diagnostic_counts = {}
    for diagnostic in envelope["diagnostics"]:
        diagnostic_counts[diagnostic["code"]] = (
            diagnostic_counts.get(diagnostic["code"], 0) + 1)
    bounds = envelope["historyBounds"]
    lines = [
        "# History adapter shadow report",
        "",
        "- Generated: `%s`" % envelope["adapter"]["generatedAt"],
        "- Read-only: `%s`" % envelope["adapter"]["readOnly"],
        "- Observation started: `%s`" % bounds["observationStartedAt"],
        "- Observed through: `%s`" % bounds["observedThrough"],
        "- Finalized through: `%s`" % bounds["finalizedThrough"],
        "- Retention: `%s` / deletion: `%s` / backfill: `%s`" % (
            bounds["retentionState"], bounds["deletionState"],
            bounds["backfillState"]),
        "",
        "## Record counts",
        "",
    ]
    lines += ["- %s: %d" % (name, counts[name]) for name in sorted(counts)]
    lines += ["", "## Diagnostics", ""]
    if diagnostic_counts:
        lines += ["- %s: %d" % (code, diagnostic_counts[code])
                  for code in sorted(diagnostic_counts)]
    else:
        lines.append("- none")
    lines += ["", "## Qualified complete local weeks (final ledger coverage)", ""]
    if weeks:
        lines += ["- %s .. %s" % pair for pair in weeks]
    else:
        lines.append("- none yet; keep the shadow observation cadence running")
    return "\n".join(lines) + "\n"


def run_shadow(settings_path, clock=_now):
    with open(settings_path, encoding="utf-8") as handle:
        settings = json.load(handle)
    errors = adapter.settings_errors(settings)
    if errors:
        raise SystemExit("settings invalid:\n- " + "\n- ".join(errors))
    if settings.get("historyAdapterV1") is not True:
        print(json.dumps({"status": "disabled",
                          "detail": "historyAdapterV1 is not enabled"}))
        return 0
    home = os.path.abspath(os.path.expanduser(settings["workspaceHome"]))
    store_dir = os.path.abspath(os.path.expanduser(settings["storeDir"]))

    prior_records = history_store.read_all(store_dir)
    run_seq, new_records, facts = observe_workspace(
        home, settings, prior_records, clock=clock)
    history_store.append(store_dir, new_records)

    status = "completed"
    error_text = None
    try:
        inputs = {
            "storeRecords": history_store.read_all(store_dir),
            "ledgerLines": facts["ledgerLines"],
            "ledgerIntegrity": facts["ledgerIntegrity"],
            "board": facts["board"],
            "mirrors": facts["mirrors"],
            "generatedAt": _iso(clock()),
        }
        envelope = adapter.build_envelope(inputs, settings)
        weeks = _qualified_weeks(envelope)
        _atomic_private_write(
            os.path.join(store_dir, "envelope-latest.json"),
            (json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False)
             + "\n").encode("utf-8"))
        _atomic_private_write(
            os.path.join(store_dir, "report-latest.md"),
            _report_text(envelope, weeks).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        status = "failed"
        error_text = str(exc)[:500]
        raise
    finally:
        completion = {
            "storeSchemaVersion": history_store.STORE_SCHEMA_VERSION,
            "type": "run-completed",
            "runSeq": run_seq,
            "completedAt": _iso(clock()),
            "status": status,
            "error": error_text,
        }
        try:
            history_store.append(store_dir, [completion])
        except history_store.HistoryStoreError:
            # Never let the completion marker mask the original failure;
            # a crashed run without a marker surfaces as unknown-interval.
            if status == "completed":
                raise

    print(json.dumps({
        "status": "completed",
        "runSeq": run_seq,
        "storeDir": store_dir,
        "qualifiedWeeks": len(weeks),
        "collections": {
            name: len(envelope[name])
            for name in ("lifecycleEvents", "verificationEvents",
                         "blockerEpisodes", "diagnostics")
        },
    }, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m history.shadow",
        description="Generate a shadow history envelope from retained local "
                    "records. Reads the workspace; writes only its private "
                    "store directory.")
    parser.add_argument(
        "--settings", required=True,
        help="path to the private runtime settings JSON document")
    arguments = parser.parse_args(argv)
    return run_shadow(arguments.settings)


if __name__ == "__main__":
    sys.exit(main())
