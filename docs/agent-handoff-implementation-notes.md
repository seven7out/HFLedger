# Priority details and agent handoff implementation notes

## Outcome

Every selectable priority row now opens its exact item in Details from the row
body, keyboard, or headline. The inspector adds a product-shaped **Start work**
section with a copy action and native Mac launch actions for Codex and Claude
Code.

The handoff uses only the projected owner brief: product outcome, importance,
definition of done, incomplete product parts, risks, need-by date, and observed
status. It explicitly tells the agent to read available project instructions,
resource packets, specifications, research, evidence, and prior work; verify
that the task remains unfinished; inspect current state; and research unresolved
facts from authoritative sources when needed. Evidence diagnostics, source
excerpts, local notes, paths, and command text are excluded. The prompt repeats
that it is context rather than authority and that protected or production
action still requires explicit permission. Browser copies and Codex launches
begin with a `/goal` objective tied to the verified product outcome and
definition of done. Claude Code launches omit that Codex-only command.

## Native boundary

The served board receives one page-to-native capability: a closed
`codex | claude-code` target. The capability applies only to the finite
loopback port range owned by the host, while board navigation is restricted to
the exact active origin. The native host resolves an executable from a fixed
list of conventional locations and passes only that validated path to a static
Terminal script. No prompt, task title, project name, source reference, or
observed path enters native process arguments.

All settings commands are now explicitly permissioned. The board cannot call
them, and its agent-launch capability grants no Tauri core, filesystem, shell,
dialog, or settings permission.

## Deviations log

- The launch controls do not submit the prompt automatically. They copy it and
  open a blank local session so the owner can review, paste, and submit it.
  Automatically executing observed text would turn workspace data into agent
  authority.
- Native launch is Mac-only. Browser-only serving keeps the copy action and
  explains that session launch is available in the Mac app.
- The session opens in Terminal's normal starting directory. HFLedger does not
  pass an observed or inferred project path across the native boundary.
