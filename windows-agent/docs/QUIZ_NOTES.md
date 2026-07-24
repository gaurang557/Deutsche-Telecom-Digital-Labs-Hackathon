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

## Permission & safety model (M12) — deep dive

This is the module's safety core, now **implemented** (`policy/deterministic.py`,
`policy/confirmation.py`, and the dispatcher's policy gate). Expect the reviewer
to push hardest here.

**Q: Why deterministic authorization instead of letting the LLM decide?**
Two properties the reviewer cares about: **reproducibility** and
**explainability**. `DeterministicPolicy` computes risk/outcome/`rule_id` as a
pure function of the action's `type` + `parameters`, so the same action always
yields the same verdict (no temperature, no drift), and every verdict carries a
stable `rule_id` + human-readable `reason` you can audit and test. An LLM is
neither reproducible nor a trustworthy authority — it can be talked into things.
So the LLM *proposes*; deterministic code *authorizes*.

**Q: What do the four outcomes mean, and what are the rule_ids?**
- **ALLOW** — run it (and verify if it modifies state). Reads → `R-READ-ALLOW`;
  create-new-state → `R-CREATE-ALLOW` (allowed but logged).
- **CONFIRM** — do not run until an explicit, single-use, action-bound token is
  presented. Overwrite-in-place → `R-OVERWRITE-CONFIRM`; `file.move` →
  `R-MOVE-CONFIRM`; `file.delete` → `R-DELETE-CONFIRM`; leaves-the-machine
  (send/submit/publish/purchase) → `R-CONSEQUENTIAL-CONFIRM`.
- **DENY** — never run → `R-FORBIDDEN-DENY` (shell/registry/`code.eval` and
  commands sourced from untrusted content). There is no confirmable version.
- **CLARIFY** — unrecognised/ambiguous type → ask the user → `R-UNKNOWN-CLARIFY`.
  Unknown types fail safe to a HIGH floor so they never slip through as "safe".

**Q: How do confirmation tokens resist prompt-injection / replay / mutated-action
reuse?**
A confirmation is bound to the EXACT action via `action_hash` — a canonical
SHA-256 over `type` + `target` + **sorted** `parameters`.
`ConfirmationStore.validate(token, action)` passes only if the token exists, is
unused, is within its TTL (default 300s), AND the re-derived hash matches — then
it **burns** the token.
- **Mutated action** (confused deputy): approve "delete `report.tmp`", attacker
  swaps in "delete `payroll.xlsx`" → different hash → rejected. Crucially the
  failed attempt does **not** burn the token, so the legitimate action can still
  be confirmed.
- **Replay:** the token is single-use; a second `validate` returns False.
- **Expiry:** past the TTL it fails closed.
Net: an approval for action X can never authorize a mutated X′, and can never be
reused.

**Q: Why does risk live in the policy, not on the `Action`?**
Authority the LLM can write is authority the LLM can forge. `Action` uses
`extra="forbid"`, so a model can't attach `risk`/`permission`/`confirmation` — the
authority has nowhere to live except the deterministic engine. Risk is *derived*
from the action, never *declared* on it (a delete stays HIGH even if a parameter
literally says `risk=none` — asserted in tests).

**Q: How is determinism ("same inputs → same decision") guaranteed?**
Classification reads only `type` + `parameters` — never live disk state, file
contents, or retrieved evidence (all untrusted DATA). The decision fields
(outcome / risk / `rule_id` / `reason` / `action_hash`) are pure; only the
`confirmation_token` and `decision_id` are per-call nonces, and those are
deliberately *not* part of the safety verdict. So a read of a scary-looking file
is still ALLOW, and classifying a delete never touches the filesystem.

**Q: Where does the dispatcher fit — and how does the confirmation handshake
work end-to-end?**
The dispatcher obeys the decision and never self-authorizes.
`dispatch(action, *, confirmation_token=None)`: ALLOW executes; DENY/CLARIFY
short-circuit (executor never runs); CONFIRM returns `needs_confirmation` with a
freshly-minted token on the first call, and executes on the re-dispatch only when
that valid single-use token (bound to the exact action) is supplied — emitting
`confirmation_accepted`, or `confirmation_rejected` then
`policy_confirmation_required` if a bad token was presented. One path means safety
can never be bypassed by an individual executor.

---

## Honesty caveat for the quiz

Implemented today: the execution pipeline (M0/M1), the `file.*` / `pdf.*` /
`spreadsheet.*` / `document.*` / `presentation.*` executors with independent
verification (M2–M7), and the deterministic **policy + confirmation** safety core
(M12). Still **designed but not yet coded** (see `../docs/ARCHITECTURE.md`):
speech (M5), the planning LLM / tool integration (M14), desktop understanding
(M9), browser (M10), full pause/resume/correction state (M13), and the shared
persistent-audit / redaction system (M11, team-owned). This is deliberate
milestone ordering: prove the safe execution + authorization core before wiring
the model and the microphone.
