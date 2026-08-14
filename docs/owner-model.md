# Owner model

HFLedger governs the interrupt channel between AI agents and a product owner.
The owner is not expected to read code, review implementation mechanics, or
interpret delivery tooling. Every owner-facing prompt must instead ask for one
of five kinds of product judgment.

## The five judgment zones

1. **Contribute product ideas.** The owner supplies direction and chooses among
   prepared product options. The corresponding card kind is `idea_pick`.
2. **Keep production healthy.** The owner judges a user-visible release or a
   production problem. The test site is explicitly allowed to break while work
   is being prepared. The release-gate card kind is `outcome_review`; production
   health is also the first line on Today.
3. **Judge safety, security, legal, and content risk.** The owner evaluates the
   exact content or data practice at issue. The corresponding card kind is
   `risk_card`.
4. **Keep agents unblocked.** The owner sees when an automated process has died,
   starved, or failed silently, plus the useful action available to them. The
   corresponding card kind is `stuck_alarm`.
5. **Set priorities.** The owner reviews the top product builds, changes their
   order, or kills work that is no longer worth doing. The corresponding card
   kind is `priority_review`.

These kinds extend the existing admitted decision and action plane. They do not
create a second owner lane or weaken stable identity, deduplication, provenance,
snooze, completion, or resolution rules. Legacy admitted decisions and manual
actions remain valid; newly typed cards add a `cardKind` and kind-specific
product fields.

For display continuity, an untyped legacy production decision appears with
production outcomes, a protected or irreversible decision with risk judgments,
a manual action with agent blockers, and any other legacy decision with ideas
to choose. This projection does not rewrite the historical record.

## Plain product language

The primary content of a card must describe a product outcome, user experience,
production condition, concrete risk, stopped capability, or priority choice.
It must stand on its own for an owner who cannot evaluate code.

Diffs, pull requests, branch names, commit lists, check names, stack traces,
file paths, and similar implementation details are not primary evidence. They
may appear only as secondary drill-down entries in `footnoteLinks`. Validation
rejects code-shaped language in the primary fields of a typed card. Labels for
links and screenshots must still explain the product evidence they contain.

## Card vocabulary

| Card kind | Required product content | Owner response |
| --- | --- | --- |
| `idea_pick` | The idea in `idea`, prepared `options` with one-line product `description` values, and a reasoned recommendation | Pick one prepared direction or ask for context |
| `outcome_review` | `userChange`, product `evidenceLinks`, one-line `testEvidenceSummary`, and a credible `rollback` for risky releases | Release the outcome or hold it |
| `risk_card` | The exact quoted content or concrete data practice in `riskSubject`, its consequence, and a recommendation | Choose the proposed risk posture or a safer alternative |
| `stuck_alarm` | What stopped in `stopped`, when in `stoppedSince`, and the useful `ownerAction`—including “No action needed” when agents can recover | Mark handled, skip, snooze, or ask for context |
| `priority_review` | The top queued `builds`, each with a stable id and one-line product `description`, in the recommended order | Reorder the surviving builds, kill any of them, and submit the review |

`evidenceLinks` are visible product evidence such as a screenshot, preview, or
user-facing acceptance record. Technical or implementation references belong
in `footnoteLinks` and stay visually secondary.

## Surface mapping

Today begins with one plain production-health sentence: **Healthy**, or
**Degraded** followed by one sentence naming the user-visible reason. Next it
shows the number of open owner cards grouped by the five card kinds. It then
shows product flow in this exact order: **Ideas waiting on pick → Being specced
→ Being built → On the test site → Shipped to production**.

The test-site stage uses neutral styling even when work there is failing;
breakage is part of preparation. Only a production failure receives alarm
styling. Card counts and flow labels use the vocabulary above, while any source
or implementation detail remains available only through secondary links or the
existing evidence inspector.

When the native production monitor is enabled, its observation replaces the
stored production-health snapshot on Today without changing the authoritative
workspace. The primary sentence remains product-shaped. “Checked a minute ago”
is secondary context; endpoint addresses, response bodies, status codes, and
network errors never become owner-facing copy. One missed check is a retry, not
an incident. Three consecutive failures mark production degraded, one success
recovers it, and a stale monitor says that monitoring stopped updating.

The Decision Deck renders each typed card in the same product language. It
retains recommendation acceptance, snooze, context requests, completion, and
provenance. `priority_review` adds its bounded reorder-and-kill response without
granting any deployment or production-write authority.
