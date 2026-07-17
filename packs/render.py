"""Strict, deterministic rendering for supported agent runtimes."""

import hashlib
import json
import os
import re
import shlex
import unicodedata

from core import store
from .adapters import RUNTIME_FILES


ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_ROOT = os.path.join(ROOT, "templates")
TOKEN_RE = re.compile(r"{{([A-Z][A-Z0-9_]*)}}")
def _safe_text(value, label, limit=240):
    if not isinstance(value, str):
        raise ValueError("%s must be text" % label)
    text = "".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in value)
    text = " ".join(text.strip().split()).replace("`", "'")
    if not text or len(text) > limit or "{{" in text or "}}" in text:
        raise ValueError("%s is unsafe for an instruction pack" % label)
    return text


def _safe_home(home):
    path = os.path.abspath(home)
    if any(char in path for char in "\r\n\x00`") or "{{" in path or "}}" in path:
        raise ValueError("HFLedger home is unsafe for an instruction pack")
    return path


def _variables(home, config):
    automation = config["automation"]
    work = automation["workPolicy"]
    repositories = automation["repositories"]
    repository_lines = [
        "- `%s`: `%s` (stage `%s`; production `%s`)" % (
            repository["id"], repository["slug"], repository["stageBranch"],
            repository["productionBranch"])
        for repository in repositories
    ] or ["- No repository collector is configured."]
    safe_home = _safe_home(home)
    return {
        "PROJECT": _safe_text(config["project"], "project"),
        "OWNER_ROLE": _safe_text(automation["ownerRole"], "ownerRole", 80),
        "LEDGER_HOME": safe_home,
        "LEDGER_HOME_SHELL": shlex.quote(safe_home),
        "READY_STATUS": _safe_text(work["readyStatus"], "readyStatus", 120),
        "REVIEW_STATUS": _safe_text(work["reviewStatus"], "reviewStatus", 120),
        "STAGE_MERGE": "enabled" if work["allowStageMerge"] else "disabled",
        "REPOSITORIES": "\n".join(repository_lines),
    }


def _render(template, variables, name):
    tokens = set(TOKEN_RE.findall(template))
    unknown = sorted(tokens - set(variables))
    if unknown:
        raise ValueError("template %s has unknown token(s): %s" % (name, ", ".join(unknown)))
    rendered = TOKEN_RE.sub(lambda match: variables[match.group(1)], template)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("template %s contains an invalid or unresolved token" % name)
    return rendered


def _write_new(path, payload, force):
    if os.path.lexists(path) and not force:
        raise ValueError("refusing to overwrite generated file: %s" % path)
    if os.path.islink(path):
        raise ValueError("refusing to write through symlink: %s" % path)
    store._atomic_write(path, payload.encode("utf-8"), mode=0o600)


def _unsafe_target(path, output_root):
    if os.path.islink(output_root):
        return "symlink"
    relative = os.path.relpath(path, output_root)
    current = output_root
    for part in (() if relative == "." else relative.split(os.sep)):
        current = os.path.join(current, part)
        if os.path.islink(current):
            return "symlink"
    if os.path.lexists(path) and not os.path.isfile(path):
        return "non-file"
    return None


def render_packs(home, output_root=None, runtimes=None, force=False):
    """Render selected runtime packs and return their manifest."""
    home = store.resolve_home(home)
    config = store.load_config(home)
    automation = config.get("automation")
    if automation is None:
        raise ValueError("automation config is required to render packs")
    selected = runtimes or automation["packs"]["runtimes"]
    if not isinstance(selected, list) or not selected:
        raise ValueError("at least one runtime is required")
    if len(selected) != len(set(selected)) or any(item not in RUNTIME_FILES for item in selected):
        raise ValueError("runtimes must be a unique list of generic/claude-code")
    output_root = os.path.abspath(output_root or os.path.join(home, "generated", "packs"))
    if os.path.commonpath((home, output_root)) != home:
        raise ValueError("pack output must remain inside the HFLedger data directory")
    variables = _variables(home, config)
    planned = []
    for runtime in selected:
        for template_name, relative in RUNTIME_FILES[runtime].items():
            template_path = os.path.join(TEMPLATE_ROOT, template_name)
            with open(template_path, encoding="utf-8") as handle:
                rendered = _render(handle.read(), variables, template_name)
            target = os.path.join(output_root, runtime, relative)
            if os.path.commonpath((output_root, os.path.abspath(target))) != output_root:
                raise ValueError("generated path escapes output root")
            planned.append((runtime, relative, target, rendered))
    manifest_path = os.path.join(output_root, "manifest.json")
    all_targets = [target for _, _, target, _ in planned] + [manifest_path]
    unsafe = [(target, _unsafe_target(target, output_root)) for target in all_targets]
    unsafe = [(target, reason) for target, reason in unsafe if reason]
    if unsafe:
        raise ValueError("refusing unsafe generated %s target: %s" % (unsafe[0][1], unsafe[0][0]))
    conflicts = [target for target in all_targets if os.path.lexists(target)]
    if conflicts and not force:
        raise ValueError("refusing to overwrite generated file(s): %s" % ", ".join(conflicts))
    files = []
    for runtime, relative, target, rendered in planned:
        _write_new(target, rendered, force)
        files.append({
            "runtime": runtime,
            "path": os.path.relpath(target, output_root),
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        })
    manifest = {"schemaVersion": 1, "project": config["project"], "files": files}
    _write_new(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n", force)
    return {"output": output_root, "manifest": manifest_path, "files": files}
