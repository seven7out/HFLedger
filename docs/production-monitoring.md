# Continuous production monitoring

HFLedger for Mac can replace a workspace's stored production-health snapshot
with a current, read-only observation. The purpose is narrow: answer “is
production healthy?” for a non-developer owner without asking them to interpret
status codes, network errors, or deployment machinery.

## Owner contract

Today keeps one primary product sentence:

- **Healthy — The live service is responding normally.**
- **Degraded — The live service is not responding to its health check.**
- **Degraded — Production monitoring has stopped updating.**

When available, “Checked a minute ago” appears as quiet secondary context. A
missed check does not immediately create an alarm: the monitor retries twice,
then marks production degraded after the third consecutive failure. One
successful check restores healthy status. Test and staging systems are outside
this monitor; failures there remain allowed to break.

## Authority and privacy boundary

Monitoring is configured per workspace in native Settings. The health address
is stored only in mode-`0600` app-private files. It is not added to the
workspace, board, ledger, browser-facing projection, logs, diagnostics text, or
public artifacts. The native host passes the engine only the path to one
private monitor file.

The probe:

- requires HTTPS;
- refuses credentials, queries, fragments, and redirects;
- uses a five-second timeout;
- reads at most one response byte and retains no response body; and
- treats only a direct `2xx` response as success.

The engine exposes only `state`, a fixed plain-language `summary`, monitor
state, and the last checked/healthy timestamps. Network exceptions, response
content, and the configured address never enter Today.

## Lifecycle

The first check starts with the selected workspace. Checks repeat once per
minute while HFLedger is running, including while its windows are closed. An
explicit app quit stops both the local engine and monitoring. Launch at Login
is the supported way to resume monitoring after sign-in; HFLedger does not
install a second hidden service.

Changing the health address restarts only the selected local engine. Disabling
monitoring removes the per-workspace monitor file and returns Today to the
workspace's stored production-health snapshot. Neither operation modifies
`board.json` or `ledger.jsonl`.
