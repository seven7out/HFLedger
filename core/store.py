"""Configuration, path resolution, and the board's only mutation path."""

import copy
import datetime
import fcntl
import json
import os
import shutil
import tempfile
import threading

from . import admission, ledger, schema


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class BoardValidationError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("board failed validation (%d error(s)):\n- %s" % (
            len(self.errors), "\n- ".join(self.errors)))


def resolve_home(explicit=None):
    """Resolve an explicit path, then environment overrides, then platform defaults."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    override = os.environ.get("LEDGER_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.abspath(os.path.join(os.path.expanduser(xdg), "ledger"))
    return os.path.abspath(os.path.expanduser("~/.ledger"))


def load_config(home):
    path = os.path.join(home, "config.json")
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        raise ValueError("Ledger data directory is not initialized: %s" % home)
    except (OSError, ValueError) as exc:
        raise ValueError("config.json is unreadable: %s" % exc)
    errors = config_errors(config)
    if errors:
        raise ValueError("config.json is invalid:\n- " + "\n- ".join(errors))
    return config


def config_errors(config):
    if not isinstance(config, dict):
        return ["config must be a JSON object"]
    errors = []
    if config.get("version") != 1:
        errors.append("version must be 1")
    if not isinstance(config.get("project"), str) or not config.get("project", "").strip():
        errors.append("project must be non-empty text")
    retention = config.get("backupRetention")
    if not isinstance(retention, int) or isinstance(retention, bool) or retention < 1:
        errors.append("backupRetention must be a positive integer")
    limit = config.get("quarantineLimit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        errors.append("quarantineLimit must be a positive integer")
    registry = config.get("writerRegistry")
    if not isinstance(registry, dict) or not registry:
        errors.append("writerRegistry must be a non-empty object")
    else:
        for actor, writer in registry.items():
            if not isinstance(actor, str) or not actor:
                errors.append("writer names must be non-empty text")
                continue
            actions = writer.get("actions") if isinstance(writer, dict) else None
            if not isinstance(actions, dict) or not actions:
                errors.append("writerRegistry.%s.actions must be a non-empty object" % actor)
                continue
            for action, mode in actions.items():
                if not isinstance(action, str) or not action:
                    errors.append("writerRegistry.%s has an invalid action name" % actor)
                if mode not in ("reconcile", "audit-only"):
                    errors.append("writerRegistry.%s.%s must be reconcile or audit-only" % (actor, action))
    configured = config.get("schema")
    if not isinstance(configured, dict):
        errors.append("schema must be an object")
    else:
        for name in ("statuses", "drawers", "protectedClasses", "countedCollections"):
            values = configured.get(name)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                errors.append("schema.%s must be a list of non-empty strings" % name)
        extra = configured.get("extraSections")
        allowed_types = {"object", "array", "string", "number", "boolean"}
        if (not isinstance(extra, dict) or any(
                not isinstance(value, str) or value not in allowed_types
                for value in extra.values())):
            errors.append("schema.extraSections must map names to JSON type names")
        gates = configured.get("gateClasses")
        if not isinstance(gates, dict):
            errors.append("schema.gateClasses must be an object")
        else:
            for ask_type in ("decision", "action"):
                values = gates.get(ask_type)
                if not isinstance(values, list) or not values or any(
                        not isinstance(value, str) or not value for value in values):
                    errors.append("schema.gateClasses.%s must be a non-empty string list" % ask_type)
    return errors


def _atomic_write(path, payload, mode=0o600):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".ledger-write-", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def initialize(home, project="Ledger workspace"):
    """Create a new data directory without coupling it to the engine repository."""
    home = resolve_home(home)
    os.makedirs(home, exist_ok=True)
    conflicts = [name for name in ("config.json", "board.json", "ledger.jsonl")
                 if os.path.exists(os.path.join(home, name))]
    if conflicts:
        raise ValueError("refusing to overwrite initialized data file(s): %s" % ", ".join(conflicts))
    for name in ("locks", "backups", "reports"):
        os.makedirs(os.path.join(home, name), exist_ok=True)
    config = schema.default_config(project)
    board = schema.default_board(project)
    config_payload = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    board_payload = (json.dumps(board, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_write(os.path.join(home, "config.json"), config_payload)
    _atomic_write(os.path.join(home, "board.json"), board_payload)
    ledger_path = os.path.join(home, "ledger.jsonl")
    fd = os.open(ledger_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(home, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return home


def _process_lock(path):
    with _LOCKS_GUARD:
        lock = _LOCKS.get(path)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[path] = lock
        return lock


class BoardStore:
    """Locked load-mutate-validate-replace transaction for one board."""

    def __init__(self, home, config=None):
        self.home = resolve_home(home)
        self.config = config or load_config(self.home)
        self.path = os.path.join(self.home, "board.json")
        self.lock_path = os.path.join(self.home, "locks", "board.lock")

    def load(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def _snapshot_entries(self):
        lines = ledger.snapshot_lines(self.home)
        return ledger.parse_lines(lines, self.config)

    def _validate_provenance(self, board, entries):
        errors = []
        try:
            cursor_line = ledger.validate_cursor(board, entries)
        except ValueError as exc:
            return [str(exc)]
        decisions = board.get("decisions", {})
        groups = []
        if isinstance(decisions, dict):
            groups = [("items", decisions.get("items", [])), ("resolved", decisions.get("resolved", []))]
        seen_completion_lines = {}
        for bucket, items in groups:
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                provenance = item.get("ledgerProvenance")
                line = provenance.get("line") if isinstance(provenance, dict) else None
                if not isinstance(line, int) or line < 1 or line > len(entries):
                    errors.append("decision %r has ledger provenance outside the ledger" % item_id)
                    continue
                if line > cursor_line:
                    errors.append("decision %r points past the processed ledger cursor" % item_id)
                entry = entries[line - 1]
                digest = provenance.get("entrySha256")
                if digest != ledger.entry_digest(entry):
                    errors.append("decision %r ledger provenance digest does not match" % item_id)
                    continue
                if (entry.get("action") != "decision_added" or
                        entry.get("authorization") != admission.POLICY_VERSION):
                    errors.append("decision %r is not anchored to an admitted creation event" % item_id)
                    continue
                package = entry.get("extra")
                if admission.package_fingerprint(package) != admission.package_fingerprint(item):
                    errors.append("decision %r does not match its admitted ledger package" % item_id)
                if item.get("added") != str(entry.get("ts", ""))[:10] or item.get("addedEstimated") is not False:
                    errors.append("decision %r has ledger-inconsistent added metadata" % item_id)
                if bucket == "resolved" and not (
                        isinstance(item.get("completionLedgerProvenance"), dict) or
                        isinstance(item.get("resolutionLedgerProvenance"), dict)):
                    errors.append("resolved decision %r lacks valid outcome provenance" % item_id)

        seen_resolution_lines = {}
        resolved_items = decisions.get("resolved", []) if isinstance(decisions, dict) else []
        for item in resolved_items if isinstance(resolved_items, list) else []:
            if not isinstance(item, dict) or item.get("resolutionLedgerProvenance") is None:
                continue
            provenance = item.get("resolutionLedgerProvenance")
            line = provenance.get("line") if isinstance(provenance, dict) else None
            if (not isinstance(line, int) or isinstance(line, bool) or
                    not 1 <= line <= len(entries)):
                errors.append("resolved decision %r resolution line is outside the ledger" % item.get("id"))
                continue
            if line > cursor_line:
                errors.append("resolved decision %r resolution points past the processed cursor" % item.get("id"))
            if line in seen_resolution_lines:
                errors.append("resolution ledger line %d is reused by %r and %r" % (
                    line, seen_resolution_lines[line], item.get("id")))
            else:
                seen_resolution_lines[line] = item.get("id")
            entry = entries[line - 1]
            if provenance.get("entrySha256") != ledger.entry_digest(entry):
                errors.append("resolved decision %r resolution digest does not match" % item.get("id"))
                continue
            if ledger.decision_resolution_errors(entry, self.config):
                errors.append("resolved decision %r is not backed by a valid resolution event" % item.get("id"))
                continue
            extra = entry.get("extra", {})
            if (extra.get("id") != item.get("id") or
                    extra.get("resolution") != item.get("resolution") or
                    extra.get("evidence") != item.get("resolutionEvidence") or
                    extra.get("selectedOption") != item.get("selectedOption") or
                    item.get("resolvedDate") != entry.get("ts", "")[:10]):
                errors.append("resolved decision %r does not match its resolution event" % item.get("id"))

        completion_groups = []
        if isinstance(decisions, dict):
            completion_groups.extend((
                ("decisions.items", decisions.get("items", [])),
                ("decisions.resolved", decisions.get("resolved", [])),
            ))
        completion_groups.extend(
            (name, board.get(name, [])) for name in
            ("queue", "inbox", "ownerTasks", "retriage", "unmatchedCompletions"))
        for section, items in completion_groups:
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                provenance = item.get("completionLedgerProvenance")
                completion_marked = any(field in item for field in (
                    "completionDisposition", "completionEvidence", "completionSource",
                    "completionActor"))
                if provenance is None:
                    if completion_marked:
                        errors.append("%s item %r has completion metadata without ledger provenance" % (
                            section, item.get("id")))
                    continue
                if not isinstance(provenance, dict):
                    errors.append("%s item %r has invalid completion provenance" % (
                        section, item.get("id")))
                    continue
                line = provenance.get("line")
                if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= len(entries):
                    errors.append("%s item %r completion line is outside the ledger" % (
                        section, item.get("id")))
                    continue
                if line > cursor_line:
                    errors.append("%s item %r completion points past the processed cursor" % (
                        section, item.get("id")))
                location = "%s item %r" % (section, item.get("id"))
                if line in seen_completion_lines:
                    errors.append("completion ledger line %d is reused by %s and %s" % (
                        line, seen_completion_lines[line], location))
                else:
                    seen_completion_lines[line] = location
                entry = entries[line - 1]
                if provenance.get("entrySha256") != ledger.entry_digest(entry):
                    errors.append("%s completion digest does not match" % location)
                    continue
                if ledger.completion_errors(entry, self.config):
                    errors.append("%s is not backed by a valid completion event" % location)
                    continue
                extra = entry.get("extra", {})
                if section == "unmatchedCompletions":
                    matches = (extra.get("target") == item.get("target") and
                               extra.get("targetType") == item.get("targetType"))
                    if (item.get("action") != entry.get("action") or
                            item.get("evidence") != extra.get("evidence") or
                            item.get("completionSource") != extra.get("source") or
                            item.get("actor") != entry.get("actor") or
                            item.get("recordedAt") != entry.get("ts")):
                        matches = False
                else:
                    matches = (extra.get("targetType") == "id" and
                               extra.get("target") == item.get("id"))
                    matches = matches or (
                        extra.get("targetType") == "key" and
                        isinstance(item.get("dedupeKey"), str) and
                        admission.normalize_key(extra.get("target")) ==
                        admission.normalize_key(item.get("dedupeKey")))
                    expected_disposition = (
                        "completed" if entry.get("action") == "owner_completed" else "skipped")
                    if (item.get("completionDisposition") != expected_disposition or
                            item.get("completionEvidence") != extra.get("evidence") or
                            item.get("completionSource") != extra.get("source") or
                            item.get("completionActor") != entry.get("actor")):
                        matches = False
                if not matches:
                    errors.append("%s completion event does not match its board target and metadata" % location)
        return errors

    def validate(self, board, previous=None, entries=None):
        errors, warnings = schema.validate(board, self.config)
        if previous is not None:
            errors.extend(schema.validate_transition(previous, board, self.config))
        try:
            entries = self._snapshot_entries() if entries is None else entries
            errors.extend(self._validate_provenance(board, entries))
        except (OSError, ValueError) as exc:
            errors.append("ledger validation failed: %s" % exc)
        return errors, warnings

    def validate_current(self):
        try:
            board = self.load()
        except (OSError, ValueError) as exc:
            return ["board.json is unreadable: %s" % exc], []
        return self.validate(board)

    def _backup(self, raw):
        os.makedirs(os.path.join(self.home, "backups"), exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = os.path.join(self.home, "backups", "board-%s.json" % stamp)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                if written <= 0:
                    raise OSError("short backup write")
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        backups = sorted(
            os.path.join(self.home, "backups", name)
            for name in os.listdir(os.path.join(self.home, "backups"))
            if name.startswith("board-") and name.endswith(".json")
        )
        retention = self.config["backupRetention"]
        for old_path in backups[:-retention]:
            os.unlink(old_path)
        return path

    def update(self, mutator):
        """Hold both process and file locks across the complete board transaction."""
        if not callable(mutator):
            raise TypeError("mutator must be callable")
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        process_lock = _process_lock(self.path)
        with process_lock:
            with open(self.lock_path, "a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    with open(self.path, "rb") as handle:
                        raw = handle.read()
                    try:
                        before = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError) as exc:
                        raise BoardValidationError(["existing board is unreadable: %s" % exc])
                    self._backup(raw)
                    board = copy.deepcopy(before)
                    result = mutator(board)
                    if isinstance(board.get("meta"), dict):
                        board["meta"]["updated"] = schema.utc_now()
                    schema.refresh_generated(board)
                    entries = self._snapshot_entries()
                    errors, warnings = self.validate(board, previous=before, entries=entries)
                    if errors:
                        raise BoardValidationError(errors)
                    payload = (json.dumps(board, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                    _atomic_write(self.path, payload)
                    return result, warnings
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
