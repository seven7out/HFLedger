# Launch kit

These drafts lead with HFLedger's protocol wedge, state current limits plainly, and avoid positioning it as another general agent runner.

Replace `REPOSITORY_URL` only after the public repository exists. Re-run the public-clone demo before posting.

## Show HN

Title:

> Show HN: HFLedger – rate-limit and audit the interrupt channel between agents and you

Body:

> I built HFLedger because my agents were getting better at doing work but still treated every uncertainty as permission to interrupt me. The result was approval fatigue, vague “what should I do?” messages, and completed manual tasks returning days later as if they were still open.
>
> HFLedger is a local-first, agent-agnostic protocol for that boundary. An agent cannot escalate a decision unless it supplies 2–3 options, a reasoned recommendation, the risk, reversibility, rollback, stable-key dedupe, and the analysis already completed. Owner-only actions need exact instructions and completion proof. “Already done” and “skip it” are durable events too.
>
> Underneath is a validated JSON board, append-only JSONL ledger, locked atomic writer, fail-closed reconciler, and a phone-sized decision deck. Optional collectors read GitHub facts and local-file metadata, but their output is explicitly untrusted and grants no authority.
>
> It is Python 3.9+ standard library, MIT, POSIX, clone-based, and loopback-only by default. It does not run your agents or deploy code. Any runtime that can read files and call a CLI can use it; generic and Claude Code instruction packs are included.
>
> The two-minute fictional demo reaches a real swipe with two commands. I would especially value feedback on the admission contract: which fields genuinely reduce interruptions, and which are ceremony?
>
> REPOSITORY_URL

## r/LocalLLaMA

Title:

> I open-sourced a local protocol that stops agents from escalating vague questions to you

Body:

> Most local-agent projects focus on tools, planning, or autonomous execution. I kept hitting a different bottleneck: the human interrupt queue.
>
> HFLedger is a dependency-free local protocol and reference UI. It rejects vague escalations. A decision needs 2–3 real options, a recommendation with reasoning, risk/reversibility/rollback, work already done, and a stable key. Manual actions need an exact owner-only instruction and proof. Human-reported completion is also captured durably, so an agent should not keep re-serving work you already handled.
>
> The state is plain JSON + append-only JSONL. The board writer is locked/atomic, reconciliation is cursor- and provenance-checked, and the phone deck is loopback-only. There are generic and Claude Code prompt packs; any shell-capable runtime can integrate. Optional `gh` and local-file collectors are read-only and marked untrusted.
>
> Current limits are intentional: POSIX, clone-based install, no remote UI auth, no Windows writer, and no production automation. The repository includes a fictional two-minute swipe demo and 100+ standard-library tests.
>
> I am looking for feedback from people running multiple local agents: does a strict admission gate reduce approval fatigue, or would you prefer a smaller contract?
>
> REPOSITORY_URL

## Agent-tooling roundup blurb

> HFLedger is a local-first, agent-agnostic protocol for governing the AI-agent-to-human interrupt channel. It admits only structured decisions or exact owner-only actions, captures “done” and “skip” reports durably, and reconciles everything through a validated JSON board and append-only ledger. The MIT, standard-library reference includes a loopback decision deck, generic and Claude Code instruction packs, and read-only GitHub/local metadata collectors. A fictional first swipe takes about two minutes. REPOSITORY_URL

## Short social post

> Agents need a rate limit on human attention. HFLedger is a local protocol that rejects vague escalations, requires options + recommendation + risk/rollback, and durably captures “already done.” Plain JSON/JSONL, locked writes, phone decision deck, agent-agnostic, MIT. Two-minute demo: REPOSITORY_URL

## Likely questions

### Why not use GitHub Issues or a task board?

They can store the result, but they do not enforce the admission contract, completion-side capture, immutable event provenance, or a fail-closed single-writer data model. HFLedger can link to an issue; it governs when that issue becomes a human interruption.

### Is this an agent framework?

No. It neither plans nor executes general work. It is a protocol and local reference client that other runtimes call.

### Why JSON files?

They keep the protocol inspectable, portable, versionable in a private data repository, and usable from any shell-capable runtime. Locking, validation, backups, and atomic replace address the usual multi-writer hazards.

### Can I expose the deck remotely?

Not with the reference server alone. It is loopback-only and has no remote authentication contract. An unauthenticated proxy is unsupported.

### Does it stop prompt injection?

It does not sandbox an agent. Collectors bound and label external observations, omit file contents, and grant no authority; runtime instructions must preserve that trust boundary.

### Why the long decision schema?

The friction is intentional: it forces an agent to finish reversible analysis before consuming human attention. The project welcomes evidence about fields that can be removed without admitting vague asks.
