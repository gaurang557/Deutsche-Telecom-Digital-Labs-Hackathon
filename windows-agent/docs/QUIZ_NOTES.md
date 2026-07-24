# Quiz Prep Notes

Personal study notes for defending the design in a review/quiz. Detailed
per-component explanations live in the source files themselves (module
docstrings + inline comments); this file is the fast-recall cheat sheet.

---

## The anchor sentence

> **The LLM proposes actions; only deterministic code authorizes them — and we
> make that structurally true, not just a promise.**

Almost every M0 decision serves this invariant.

## How the invariant is enforced (3 layers)

1. **Schema layer:** `Action` uses Pydantic `extra="forbid"`, so it rejects any
   `risk` / `permission` / `trust` / `confirmation` / `authorization` field. The
   LLM has nowhere to put authority.
2. **Architecture layer:** authorization is a separate deterministic Policy
   Engine (M1) that the LLM never calls.
3. **Binding layer:** confirmation is bound to an `action_hash`, so approving
   one action can never authorize a different one.

## The M0 execution path (one breath)

`Action` → `Dispatcher.dispatch()` → `Registry` lookup → `Executor.execute()`
(returns `ExecutorResult`) → dispatcher bounds evidence + wraps into
`ActionResult` → back to planner. Unknown/crashing actions fail **closed**.

---

## Anticipated questions & crisp answers

**Q: How do you stop the LLM from granting itself permissions?**
Three layers: `Action` schema forbids authority fields (`extra="forbid"`);
authorization is a separate deterministic Policy Engine the LLM never invokes;
confirmation is bound to an `action_hash` so approving one action can't
authorize another.

**Q: Why async when there's no I/O yet?**
Real executors will be I/O-bound (files, PDF, browser, desktop). Retrofitting
sync→async later touches every executor and the dispatcher; paying the cost
once, upfront, avoids a painful migration.

**Q: Why two result types (`ExecutorResult` vs `ActionResult`)?**
`ExecutorResult` (internal) is what the executor knows: `success`, `evidence`,
`side_effects`, `error`. `ActionResult` (external) is what the planner needs:
`status`, `evidence`, `verification`, `error`. The split keeps executors
ignorant of planner concepts and lets the dispatcher own evidence-bounding and
verification attachment.

**Q: What happens on an unknown or crashing action?**
Fail-closed. Unknown type → `ActionResult(status=FAILED, error.code=UNKNOWN_ACTION)`.
An executor that raises is caught → `FAILED` with `EXECUTOR_ERROR`. A crash
never escapes the dispatcher.

**Q: Why a registry instead of a big if/elif in `execute_action()`?**
~55 action types would make one function unmaintainable and untestable. A
registry means "add a capability = register a handler," keeps a single
execution path for safety hooks, and makes each executor independently testable.
Unknown lookups return `None` so the dispatcher can fail safely.

**Q: Why bound evidence?**
Three reasons: (1) retrieved content is untrusted; (2) don't bloat LLM context /
cost with whole PDFs or DOM trees; (3) keep audit logs small. The dispatcher
caps string length and collection size.

**Q: Why is confirmation bound to an action hash?**
So a change to the action's type/target/parameters invalidates prior consent.
The user can never approve one thing and have another executed — critical for
interruptions/corrections mid-task.

**Q: Why deterministic risk classification instead of asking the LLM?**
Same inputs must always produce the same decision, and every decision must be
explainable (`rule_id` + `reason`). An LLM is neither reproducible nor a safe
authority.

**Q: How is prompt injection handled?**
Content from PDFs/webpages/UIs is treated as data, never commands. Injection-like
text raises an `UNTRUSTED_CONTENT_DETECTED` audit event but can never change an
authorization outcome.

**Q: What's NOT in M0, and why is that OK?**
No policy/verification/audit/state/LLM/voice/desktop yet. But the dispatcher has
the exact reserved insertion points (`validate → [policy] → [confirmation] →
execute → [verification] → [audit]`), so adding them rewires nothing. We prove
the deterministic execution core before connecting the model and microphone.

**Q: Why `expected_result` on the Action?**
It's the planner's structured statement of intended outcome, which the
verification stage (M2) turns into an assertion to independently re-observe —
because "no exception" is not proof of success.

---

## Honesty caveat for the quiz

Only **Milestone 0** (the execution contract) is implemented today. Speech,
planning-LLM, desktop understanding, state, verification, policy, and audit are
**designed** (see `../docs/ARCHITECTURE.md`) but not yet coded. This is deliberate
milestone ordering: prove the safe execution core first.
