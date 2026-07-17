"""Local-file metadata collector; file contents never enter reports."""

import fnmatch
import hashlib
import os
import re
import stat as stat_module

from .base import source_result, untrusted_summary


def _matches(relative, patterns):
    return any(
        fnmatch.fnmatchcase(relative, pattern) or
        (pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:]))
        for pattern in patterns
    )


def _root_observations(root_config):
    root = os.path.abspath(os.path.expanduser(root_config["path"]))
    if os.path.islink(root) or not os.path.isdir(root):
        raise OSError("configured root is not a readable directory")
    observations = []
    def raise_walk_error(error):
        raise error
    for directory, names, files in os.walk(
            root, topdown=True, onerror=raise_walk_error, followlinks=False):
        names[:] = sorted(
            name for name in names
            if not os.path.islink(os.path.join(directory, name)))
        for name in sorted(files):
            path = os.path.join(directory, name)
            if os.path.islink(path):
                continue
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if not _matches(relative, root_config["patterns"]):
                continue
            stat = os.stat(path, follow_symlinks=False)
            if not stat_module.S_ISREG(stat.st_mode):
                continue
            extension = os.path.splitext(relative)[1]
            if not re.fullmatch(r"\.[A-Za-z0-9]{1,19}", extension):
                extension = ""
            observations.append({
                "kind": "localFile",
                "root": root_config["id"],
                "relativePathSha256": hashlib.sha256(relative.encode("utf-8")).hexdigest(),
                "extension": extension,
                "sizeBytes": stat.st_size,
                "modifiedNs": stat.st_mtime_ns,
                "untrustedSummary": untrusted_summary(relative),
            })
            if len(observations) >= root_config["maxFiles"]:
                return observations, True
    return observations, False


def collect(config):
    settings = config["automation"]["sources"]["localFiles"]
    if not settings["enabled"]:
        return source_result("localFiles", "disabled")
    if not settings["roots"]:
        return source_result("localFiles", "degraded", error="enabled with no roots")
    observations, errors, truncated = [], [], []
    for root in settings["roots"]:
        try:
            found, hit_limit = _root_observations(root)
            observations.extend(found)
            if hit_limit:
                truncated.append(root["id"])
        except OSError as exc:
            errors.append("%s: %s" % (root["id"], exc))
    result = source_result(
        "localFiles", "degraded" if errors else "healthy", observations,
        "; ".join(errors) if errors else None)
    if truncated:
        result["truncatedRoots"] = truncated
    return result
