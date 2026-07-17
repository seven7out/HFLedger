"""Supported runtime layouts for rendered HFLedger instructions."""


RUNTIME_FILES = {
    "generic": {
        "AGENTS.md.tmpl": "AGENTS.md",
        "sweep.md.tmpl": "prompts/sweep.md",
        "work.md.tmpl": "prompts/work.md",
        "attend.md.tmpl": "prompts/attend.md",
        "status.md.tmpl": "prompts/status.md",
    },
    "claude-code": {
        "AGENTS.md.tmpl": "CLAUDE.md",
        "sweep.md.tmpl": ".claude/commands/ledger-sweep.md",
        "work.md.tmpl": ".claude/commands/ledger-work.md",
        "attend.md.tmpl": ".claude/commands/ledger-attend.md",
        "status.md.tmpl": ".claude/commands/ledger-status.md",
    },
}


__all__ = ["RUNTIME_FILES"]
