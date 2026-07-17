"""Create a Ledger home, runtime packs, and inactive scheduler definitions."""

import hashlib
import json
import os
import plistlib
import shutil
import sys

from core import schema, store
from packs import render_packs


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "ledger")
PYTHON = os.path.abspath(sys.executable)
SCHEDULE_MODES = {"none", "launchd", "systemd", "both"}


def _schedule_path():
    candidates = [
        os.path.dirname(PYTHON),
        os.path.dirname(shutil.which("gh")) if shutil.which("gh") else None,
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
        "/usr/sbin", "/sbin",
    ]
    return ":".join(dict.fromkeys(path for path in candidates if path))


def _write_generated(path, payload, force=False):
    if os.path.lexists(path):
        if os.path.islink(path):
            raise ValueError("refusing to write through symlink: %s" % path)
        if not force:
            raise ValueError("refusing to overwrite generated file: %s" % path)
    store._atomic_write(path, payload, mode=0o600)


def _unsafe_target(path, output_root):
    if os.path.islink(output_root):
        return True
    current = output_root
    relative = os.path.relpath(path, output_root)
    for part in (() if relative == "." else relative.split(os.sep)):
        current = os.path.join(current, part)
        if os.path.islink(current):
            return True
    return os.path.lexists(path) and not os.path.isfile(path)


def _systemd_quote(value):
    if any(char in value for char in "\r\n\x00"):
        raise ValueError("schedule path contains a control character")
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def generate_schedules(home, mode="none", hour=7, minute=0, force=False):
    """Generate schedule files without loading, enabling, or copying them."""
    home = store.resolve_home(home)
    if mode not in SCHEDULE_MODES:
        raise ValueError("schedule mode must be none, launchd, systemd, or both")
    if (not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23 or
            not isinstance(minute, int) or isinstance(minute, bool) or not 0 <= minute <= 59):
        raise ValueError("schedule time must be a valid hour and minute")
    output = os.path.join(home, "generated", "schedules")
    if mode == "none":
        return {"output": output, "files": [], "activated": False}
    digest = hashlib.sha256(home.encode("utf-8")).hexdigest()[:12]
    label = "org.ledger.collect.%s" % digest
    schedule_path = _schedule_path()
    planned = []
    if mode in ("launchd", "both"):
        plist = {
            "Label": label,
            "ProgramArguments": [PYTHON, CLI, "--home", home, "collect"],
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "ProcessType": "Background",
            "StandardOutPath": os.path.join(home, "reports", "collector-schedule.log"),
            "StandardErrorPath": os.path.join(home, "reports", "collector-schedule-error.log"),
        }
        plist["EnvironmentVariables"] = {"PATH": schedule_path}
        planned.append(("launchd/%s.plist" % label, plistlib.dumps(plist, sort_keys=True)))
    if mode in ("systemd", "both"):
        service_name = "ledger-collect-%s.service" % digest
        timer_name = "ledger-collect-%s.timer" % digest
        service = (
            "[Unit]\nDescription=Collect read-only Ledger observations\n\n"
            "[Service]\nType=oneshot\nUMask=0077\n%sExecStart=%s %s --home %s collect\n" % (
                "Environment=%s\n" % _systemd_quote("PATH=" + schedule_path),
                _systemd_quote(PYTHON), _systemd_quote(CLI), _systemd_quote(home)))
        timer = (
            "[Unit]\nDescription=Schedule read-only Ledger collection\n\n"
            "[Timer]\nOnCalendar=*-*-* %02d:%02d:00\nPersistent=true\nUnit=%s\n\n"
            "[Install]\nWantedBy=timers.target\n" % (hour, minute, service_name))
        planned.extend((
            ("systemd/%s" % service_name, service.encode("utf-8")),
            ("systemd/%s" % timer_name, timer.encode("utf-8")),
        ))
    conflicts = [os.path.join(output, relative) for relative, _ in planned
                 if os.path.lexists(os.path.join(output, relative))]
    unsafe = [os.path.join(output, relative) for relative, _ in planned
              if _unsafe_target(os.path.join(output, relative), output)]
    if unsafe:
        raise ValueError("refusing unsafe generated schedule target: %s" % unsafe[0])
    if conflicts and not force:
        raise ValueError("refusing to overwrite generated schedule(s): %s" % ", ".join(conflicts))
    files = []
    for relative, payload in planned:
        path = os.path.join(output, relative)
        _write_generated(path, payload, force=force)
        files.append(path)
    return {"output": output, "files": files, "activated": False}


def _parse_repository(value):
    parts = value.split(",") if isinstance(value, str) else []
    if len(parts) != 4:
        raise ValueError("repository must use ID,OWNER/REPOSITORY,STAGE,PRODUCTION form")
    return {"id": parts[0], "slug": parts[1], "stageBranch": parts[2], "productionBranch": parts[3]}


def _parse_local_root(value, patterns):
    parts = value.split("=", 1) if isinstance(value, str) else []
    if len(parts) != 2:
        raise ValueError("local root must use ID=PATH form")
    return {"id": parts[0], "path": parts[1], "patterns": patterns, "maxFiles": 200}


def configure_install(directory, project, repositories=None, local_roots=None,
                      patterns=None, runtimes=None, schedule="none", hour=7, minute=0):
    """Initialize and configure one independent Phase 3 data directory."""
    home = store.resolve_home(directory)
    if any(char in home for char in "\r\n\x00`") or "{{" in home or "}}" in home:
        raise ValueError("installation directory is unsafe for generated instructions")
    if isinstance(project, str) and ("{{" in project or "}}" in project):
        raise ValueError("project label is unsafe for generated instructions")
    repositories = [_parse_repository(value) for value in (repositories or [])]
    patterns = patterns or ["**/*.md"]
    local_roots = [_parse_local_root(value, patterns) for value in (local_roots or [])]
    runtimes = runtimes or ["generic"]
    config = schema.default_config(project)
    automation = config["automation"]
    automation["repositories"] = repositories
    automation["sources"]["github"]["enabled"] = bool(repositories)
    automation["sources"]["localFiles"]["enabled"] = bool(local_roots)
    automation["sources"]["localFiles"]["roots"] = local_roots
    automation["packs"]["runtimes"] = runtimes
    automation["schedule"]["enabled"] = schedule != "none"
    automation["schedule"]["hour"] = hour
    automation["schedule"]["minute"] = minute
    errors = store.config_errors(config)
    if errors:
        raise ValueError("installer configuration is invalid:\n- " + "\n- ".join(errors))
    if schedule not in SCHEDULE_MODES:
        raise ValueError("schedule mode must be none, launchd, systemd, or both")
    home = store.initialize(home, project=project)
    store.save_config(home, config)
    packs = render_packs(home)
    schedules = generate_schedules(home, mode=schedule, hour=hour, minute=minute)
    result = {
        "status": "installed",
        "home": home,
        "config": os.path.join(home, "config.json"),
        "packs": packs,
        "schedules": schedules,
    }
    store._atomic_write(
        os.path.join(home, "generated", "install-manifest.json"),
        (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"), mode=0o600)
    return result
