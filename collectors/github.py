"""Read-only GitHub observations collected through the authenticated gh CLI."""

import json
import subprocess
import urllib.parse
import unicodedata

from .base import CollectorError, source_result, untrusted_summary


def _run_json(argv, runner):
    try:
        completed = runner(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CollectorError("could not start gh: %s" % exc)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "gh returned no diagnostic").strip()
        raise CollectorError("gh command failed: %s" % detail[:300])
    try:
        return json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise CollectorError("gh returned invalid JSON: %s" % exc)


def _list(argv, runner, label):
    value = _run_json(argv, runner)
    if not isinstance(value, list):
        raise CollectorError("%s response was not a list" % label)
    return value


def _safe_scalar(item, name, expected, default=None):
    value = item.get(name, default) if isinstance(item, dict) else default
    return value if isinstance(value, expected) and not isinstance(value, bool) else default


def _safe_string(item, name, limit=500):
    value = item.get(name) if isinstance(item, dict) else None
    if not isinstance(value, str):
        return None
    value = "".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in value)
    value = " ".join(value.split())
    return value if value and len(value) <= limit else None


def _pr_observation(repository_id, state, item):
    return {
        "kind": "pullRequest",
        "repository": repository_id,
        "state": state,
        "number": _safe_scalar(item, "number", int),
        "url": _safe_string(item, "url"),
        "baseBranch": _safe_string(item, "baseRefName", 200),
        "headBranch": _safe_string(item, "headRefName", 200),
        "draft": item.get("isDraft") if isinstance(item, dict) and isinstance(item.get("isDraft"), bool) else None,
        "updatedAt": _safe_string(item, "updatedAt", 80),
        "mergedAt": _safe_string(item, "mergedAt", 80),
        "untrustedSummary": untrusted_summary(item.get("title") if isinstance(item, dict) else None),
    }


def _collect_repository(repository, settings, runner):
    slug = repository["slug"]
    repository_id = repository["id"]
    common = ["--repo", slug]
    pr_fields = "number,title,url,headRefName,baseRefName,isDraft,updatedAt,mergedAt"
    open_prs = _list([
        "gh", "pr", "list", *common, "--state", "open", "--limit",
        str(settings["prLimit"]), "--json", pr_fields,
    ], runner, "open pull request")
    merged_prs = _list([
        "gh", "pr", "list", *common, "--state", "merged", "--limit",
        str(settings["mergedLimit"]), "--json", pr_fields,
    ], runner, "merged pull request")
    runs = _list([
        "gh", "run", "list", *common, "--limit", str(settings["runLimit"]),
        "--json", "databaseId,workflowName,status,conclusion,url,headBranch,event,createdAt,updatedAt",
    ], runner, "workflow run")
    issues = _list([
        "gh", "issue", "list", *common, "--state", "open", "--limit",
        str(settings["issueLimit"]), "--json", "number,title,url,updatedAt",
    ], runner, "issue")
    base = urllib.parse.quote(repository["productionBranch"], safe="")
    head = urllib.parse.quote(repository["stageBranch"], safe="")
    comparison = _run_json([
        "gh", "api", "repos/%s/compare/%s...%s" % (slug, base, head),
    ], runner)
    if not isinstance(comparison, dict):
        raise CollectorError("branch comparison response was not an object")

    observations = []
    observations.extend(_pr_observation(repository_id, "open", item) for item in open_prs)
    observations.extend(_pr_observation(repository_id, "merged", item) for item in merged_prs)
    for item in runs:
        observations.append({
            "kind": "workflowRun",
            "repository": repository_id,
            "runId": _safe_scalar(item, "databaseId", int),
            "status": _safe_string(item, "status", 80),
            "conclusion": _safe_string(item, "conclusion", 80),
            "url": _safe_string(item, "url"),
            "branch": _safe_string(item, "headBranch", 200),
            "event": _safe_string(item, "event", 80),
            "createdAt": _safe_string(item, "createdAt", 80),
            "updatedAt": _safe_string(item, "updatedAt", 80),
            "untrustedSummary": untrusted_summary(
                item.get("workflowName") if isinstance(item, dict) else None),
        })
    for item in issues:
        observations.append({
            "kind": "issue",
            "repository": repository_id,
            "state": "open",
            "number": _safe_scalar(item, "number", int),
            "url": _safe_string(item, "url"),
            "updatedAt": _safe_string(item, "updatedAt", 80),
            "untrustedSummary": untrusted_summary(item.get("title") if isinstance(item, dict) else None),
        })
    observations.append({
        "kind": "branchComparison",
        "repository": repository_id,
        "productionBranch": repository["productionBranch"],
        "stageBranch": repository["stageBranch"],
        "status": _safe_string(comparison, "status", 80),
        "aheadBy": _safe_scalar(comparison, "ahead_by", int),
        "behindBy": _safe_scalar(comparison, "behind_by", int),
        "totalCommits": _safe_scalar(comparison, "total_commits", int),
        "url": _safe_string(comparison, "html_url"),
    })
    return observations


def collect(config, runner=subprocess.run):
    settings = config["automation"]["sources"]["github"]
    if not settings["enabled"]:
        return source_result("github", "disabled")
    repositories = config["automation"]["repositories"]
    if not repositories:
        return source_result("github", "degraded", error="enabled with no repositories")
    observations = []
    errors = []
    for repository in repositories:
        try:
            observations.extend(_collect_repository(repository, settings, runner))
        except CollectorError as exc:
            errors.append("%s: %s" % (repository["id"], exc))
    if errors:
        return source_result("github", "degraded", observations, "; ".join(errors))
    return source_result("github", "healthy", observations)
