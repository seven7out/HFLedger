"""Locked collector orchestration and durable report output."""

import fcntl
import json
import os
import uuid

from core import store

from . import github, local_files
from .base import SCHEMA_VERSION, utc_now


class CollectorBusyError(RuntimeError):
    pass


def _markdown(report):
    lines = [
        "# HFLedger collector report",
        "",
        "- Collection: `%s`" % report["collectionId"],
        "- Status: **%s**" % report["status"],
        "- Started: `%s`" % report["startedAt"],
        "- Completed: `%s`" % report["completedAt"],
        "",
        "External summaries are intentionally omitted from this view.",
        "",
        "| Source | Status | Observations |",
        "| --- | --- | ---: |",
    ]
    for source in report["sources"]:
        lines.append("| %s | %s | %d |" % (
            source["source"], source["status"], len(source["observations"])))
    return "\n".join(lines) + "\n"


def collect(home, config=None, github_runner=None):
    """Run configured read-only sources once; raise when another run holds the lock."""
    home = store.resolve_home(home)
    config = config or store.load_config(home)
    if config.get("automation") is None:
        raise ValueError("automation config is required to collect")
    lock_path = os.path.join(home, "locks", "collector.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectorBusyError("another collector run is active")
        try:
            started = utc_now()
            sources = [
                github.collect(config) if github_runner is None else github.collect(config, runner=github_runner),
                local_files.collect(config),
            ]
            configured = [source for source in sources if source["status"] != "disabled"]
            if any(source["status"] == "degraded" for source in configured):
                status = "degraded"
            elif not configured:
                status = "idle"
            else:
                status = "healthy"
            report = {
                "schemaVersion": SCHEMA_VERSION,
                "dataClassification": "untrusted-observations",
                "grantsAuthority": False,
                "collectionId": uuid.uuid4().hex,
                "startedAt": started,
                "completedAt": utc_now(),
                "status": status,
                "sources": sources,
            }
            reports = os.path.join(home, "reports")
            json_path = os.path.join(reports, "collector-latest.json")
            markdown_path = os.path.join(reports, "collector-latest.md")
            store._atomic_write(markdown_path, _markdown(report).encode("utf-8"), mode=0o600)
            # JSON is the authoritative commit marker and is replaced last.
            store._atomic_write(
                json_path,
                (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                mode=0o600)
            return report
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
