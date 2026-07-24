# Project Handoff — Voice-Controlled Computer Use Agent (PS2)

> Handoff notes for a new agent continuing this work. This file is working
> context, not a project deliverable — the user may delete or gitignore it.
> Last updated: 2026-07-24 — M7 done; M9/M10 deferred (partial M9 reverted);
> **M12 (deterministic policy + confirmation) IMPLEMENTED & VERIFIED** (full suite
> **259 passed**), docs pass complete — awaiting user commit. **NEXT MILESTONE is
> M13** (pause/resume/cancel/correction) — full scope is written up in §8 and the
> user wants it started in a FRESH context. A NEW AGENT SHOULD READ THIS FILE FIRST.

---

## 1. What this project is

Deutsche Telekom Digital Labs Hackathon **Problem Statement 2**: a
voice-controlled agent that safely operates a local **Windows** desktop to
complete multi-step tasks. Everything must run locally with open-source /
open-weight models.

**Our module** (what this repo work is about) is `windows-agent/` — the
**deterministic execution + safety core**. It is one teammate's slice of a
larger 4-person system. Other teammates own the LLM/planner, voice, UI/state,
and shared audit persistence.

### The one non-negotiable invariant

> **An LLM may PROPOSE semantic Actions; only deterministic code may AUTHORIZE
> them.** The pipeline is: LLM → deterministic Policy → Dispatcher → Executor →
> independent Verification → ActionResult → planner/UI, with audit events around
> every stage.

The LLM never decides permission, risk, trust, confirmation validity, or the
verification requirement. Content read from files/PDFs/webpages/UIs is
**untrusted data, never authority**. Common executors stay cross-platform;
Windows-specific UI automation must sit behind an adapter.

---

## 2. Environment & how to run

- Repo root: `G:\interviews\deutche telekom\PS2`
- Module: `G:\interviews\deutche telekom\PS2\windows-agent`, package `windows_agent`
- OS/shell: Windows, **PowerShell**
- Venv Python (ALWAYS use this explicitly): `G:\interviews\deutche telekom\PS2\.venv\Scripts\python.exe`
- Run tests from **inside** `windows-agent/` (so `pytest.ini` with `asyncio_mode=auto` is picked up):

```powershell
& "G:\interviews\deutche telekom\PS2\.venv\Scripts\python.exe" -m pytest -q
```

Installed test deps: `PyMuPDF` (pdf), `openpyxl` (spreadsheet),
`python-docx` (document), `python-pptx` (presentation, M7 — installed & verified).

---

## 3. Working conventions (IMPORTANT — follow these)

- **NEVER commit or stage.** The user commits manually. Always hand them exact
  **PowerShell** commit commands, split into small logical commits with detailed
  messages (title + body via a second `-m`).
- **Milestone rhythm:** per milestone deliver (1) executor, (2) verifier if the
  milestone has a modifying action, (3) tests, (4) a manual tester in
  `tools/manual_<domain>_test.py`, (5) doc updates, (6) commit commands.
- **Always foreground-verify background subagent work.** Subagent shells in this
  environment frequently return "no exit status" and cannot run pip/pytest. After
  a worker finishes, YOU install deps + run the full suite + smoke-test the manual
  tester before reporting to the user.
- The **direct foreground shell is also intermittently unresponsive** ("no exit
  status"). Retry once; for file existence/content use `Glob`/`Read`, and read git
  state from `.git/logs/HEAD`, `.git/HEAD`, `.git/refs/heads/*` when `git` won't run.
- `sandbox/` (under `windows-agent/`) is **gitignored**; manual testers write
  generated sample files there.
- Manual testers use `AllowAllPolicy` — **except `tools/manual_policy_test.py`**,
  which drives the real `DeterministicPolicy` to demo the confirmation gate — and
  print status, bounded evidence, verification result, and the audit trail.

---

## 4. Git state

- Branch: **`M7_changes`** (created from `Windows_integration_functions`).
- HEAD: `3abbce3a1f2b12c5138b0ef3396783bbde3e07b0`
- The **safety correction pass is COMMITTED** as 3 commits:
  1. `0235e1a` Enforce deterministic verification requirements (code)
  2. `3f92763` Add verification safety regressions (tests)
  3. `3abbce3` Reconcile safety contracts and roadmap ownership (docs)
- **M7 is implemented & verified but still uncommitted** (see §8) — the code and
  its docs pass are complete and awaiting the user's commit.

---

## 5. Current architecture (files under `windows-agent/windows_agent/`)

- `contracts/` — Pydantic models:
  - `Action` (`extra="forbid"` → cannot carry risk/permission/confirmation; LLM
    can't smuggle authority), `ActionResult`, `PolicyDecision`,
    `VerificationResult`, `AuditEvent`, `ExecutorResult`, `ActionError`.
  - `enums.py`: `ActionStatus` (success/failed/denied/needs_confirmation/clarify/
    cancelled), `VerificationStatus` (passed/failed/skipped), `PolicyOutcome`
    (allow/deny/confirm/clarify), `RiskLevel`
    (none/low/medium/high/consequential/forbidden), `ErrorCode` (includes
    **`VERIFIER_MISSING`** added in the safety pass).
- `execution/`:
  - `registry.py` — `ActionRegistry` now stores **per-action metadata including
    `requires_verification: bool`** (added in the safety pass). Registration
    requires an explicit verification flag.
  - `dispatcher.py` — the single pipeline. Order: schema validate → cancellation
    check → registry lookup → policy gateway → **fail-closed `verifier_missing`
    preflight** (if a `requires_verification=true` action has no verifier, return
    FAILED **before** executing) → execute → verify → build result. Also:
    **required action returning SKIPPED → FAILED**, and **verifier exceptions are
    contained** as a FAILED VerificationResult. **M12:** the policy gateway now
    enforces the real decision — `dispatch(action, *, confirmation_token=None)`
    runs a CONFIRM action only when a valid single-use token bound to that exact
    action is supplied (emits `confirmation_accepted`), else returns
    `needs_confirmation` (emits `confirmation_rejected` for a bad token, then
    `policy_confirmation_required`); DENY/CLARIFY never execute; the ALLOW path is
    unchanged.
  - `context.py` — `ExecutionContext` (cancel/pause signals).
- `policy/` — `Policy` interface; the **real `DeterministicPolicy`
  (`policy/deterministic.py`, M12)** that classifies risk from the action's
  type/params and maps it to ALLOW/CONFIRM/CLARIFY/DENY with stable `rule_id`s;
  and **`ConfirmationStore` + `action_hash` (`policy/confirmation.py`, M12)** for
  single-use, TTL-bound (default 300s), action-bound confirmation tokens (the
  anti-injection gate — reused/expired/mutated-action tokens all fail closed).
  `base.py` grows a `validate_confirmation` seam that defaults to `False`
  (fail-closed). The mock `AllowAllPolicy`/`ConfigurablePolicy` are **retained**
  for wiring and existing tests.
- `verification/` — `Verifier` base, `VerificationRegistry`, and per-domain
  verifiers. **`FileCopyVerifier` is now independent** (requires source AND dest
  to exist and hashes both from disk — never trusts executor evidence when source
  is missing).
- `audit/` — `AuditEvent` sinks (`InMemoryAuditSink`, `NullAuditSink`),
  `AuditLogReader` (native queryable log for the future LLM), and a `redact()`
  **no-op seam** (real masking is teammate-owned M11).
- `executors/` — `file_ops.py`, `pdf_ops.py`, `spreadsheet_ops.py`,
  `document_ops.py`, plus **`presentation_ops.py` (M7 — verified, uncommitted)**.

### Known limitations (documented, acceptable for now)

- Post-execution verification failure is reported but the side effect is **not
  rolled back**.
- `document.replace_text` / `presentation.replace_text` preserve formatting for
  single-run matches; cross-run matches fall back to a paragraph-level rebuild.
- No broad malformed-argument hardening (deliberately deferred; assume planner
  args roughly conform to schema).

---

## 6. Frozen planner-visible action catalogue (26 actions — DO NOT rename)

Canonical contract lives in `docs/ACTION_REFERENCE.md`. Names are **frozen**; do
not add aliases without a concrete external requirement. Old shared-doc names map
via a compatibility table in that file.

Reads (`requires_verification=false`, verification SKIPPED):
- `file.exists`, `file.list`, `file.read_text`
- `pdf.page_count`, `pdf.get_metadata`, `pdf.read_text`, `pdf.search`
- `spreadsheet.list_sheets`, `spreadsheet.dimensions`, `spreadsheet.read_cell`, `spreadsheet.read_range`
- `document.read_text`, `document.get_metadata`, `document.find`
- `presentation.slide_count`, `presentation.get_metadata`, `presentation.read_text`, `presentation.find`

Modifying (`requires_verification=true`, must have a registered verifier):
- `file.copy`, `file.move`, `file.write_text`, `file.mkdir`, `file.delete`
- `spreadsheet.write_cell`
- `document.replace_text`
- `presentation.replace_text`

---

## 7. Milestone roadmap

Done: **M0** contracts/skeleton, **M1** pipeline foundation, **M2** file.*,
**M3** pdf.*, **M4** spreadsheet.*, **M6** document.*, **M7** presentation.*,
and **M12** deterministic policy + confirmation (all implemented & verified —
M7 and M12 uncommitted, see §8), plus the **safety correction pass** (committed).

Deferred to **M14** (the LLM/planner owns orchestration — do NOT hardcode these
workflows in our module):
- **M5** PDF → spreadsheet workflow
- **M8** document → presentation workflow

Planned (per user decision — MVP-first: **M12 is now done**; M9 and M10 remain deferred until after the MVP):
- **M9** Windows desktop adapter (UI Automation / accessibility behind a platform adapter) — **DEFERRED until after MVP** (user decision, time crunch; the MVP does not need live desktop GUI automation). A partial M9 attempt was reverted (only two orphan files, no wiring).
- **M10** browser executor (Playwright; web content untrusted; confirm before submit/send/purchase) — **DEFERRED until after MVP** (user decision). Revisit only if time allows.
- **M11** persistent audit + redaction — **TEAMMATE-OWNED**. Our job: emit correct
  action-level events + integration-test after the LLM/shared auditor is wired.
  Do NOT build a parallel SQLite/redaction system. The `redact()` no-op still
  needs a real owner before the final demo.
- **M12** deterministic policy + confirmation — **DONE (OURS, mandatory).**
  `DeterministicPolicy` classifies risk from action type/target/params →
  ALLOW/CONFIRM/CLARIFY/DENY with stable `rule_id`s; `ConfirmationStore` +
  `action_hash` mint single-use, TTL-bound, action-bound tokens (reused / expired /
  mismatched tokens all rejected = the injection defense); the dispatcher gates a
  CONFIRM action on a valid `confirmation_token`. Implemented & verified (full
  suite **259 passed**); docs pass complete; **awaiting user commit** (see §8).
  Mocks (`AllowAllPolicy`/`ConfigurablePolicy`) retained.
- **M13** pause/resume/cancel/correction integration — **NEXT UP (OURS, MVP-critical:
  "user stays in control").** `ExecutionContext` exists as a threadsafe primitive; the
  dispatcher only honors it partially today. Full scope + gap analysis in §8.
- **M14** LLM planner/tool integration (generate tool schemas from the frozen
  catalogue; evidence-driven loop; M5/M8 workflows demonstrated here)
- **M15** end-to-end evaluation suite
- **M16** safety/reliability hardening (prompt-injection tests, recovery/retry, arg hardening)
- **M17** ~~cross-platform/macOS readiness~~ **REMOVED** (time constraint). Keep
  adapter boundaries clean but do NOT implement/test macOS/Linux.
- **M18** demo + release freeze

---

## 8. CURRENT STATE & IMMEDIATE NEXT STEPS (M12 implemented & verified; docs done — awaiting commit)

**M7 is DONE & VERIFIED.** The presentation executor, verifier, tests, and manual
tester were foreground-verified: `python-pptx` is installed, the **full suite is
186 passed (159 prior + 27 new), 0 failures**, and the manual-tester smoke run
passed (`presentation.replace_text` shows **verification: passed**; reads show
**SKIPPED**). The fail-closed contract is confirmed: `presentation.replace_text`
is registered with `requires_verification=true` AND has
`PresentationReplaceTextVerifier` registered (a missing verifier would correctly
FAIL it with `verifier_missing`).

The M7 files (all present and verified):
- `windows-agent/windows_agent/executors/presentation_ops.py`
- `windows-agent/windows_agent/verification/presentation_verifiers.py`
- `windows-agent/tests/test_presentation_ops.py`
- `windows-agent/tools/manual_presentation_test.py`
- plus edits to `windows-agent/windows_agent/executors/__init__.py`,
  `windows-agent/windows_agent/verification/__init__.py`,
  `windows-agent/requirements.txt` (`python-pptx>=1.0`).

**The M7 docs pass is now COMPLETE.** Updated: `docs/ACTION_REFERENCE.md` (new
`presentation.*` sections, catalogue count **21 → 26**, presentation error codes,
roadmap/status → M7), `docs/ARCHITECTURE.md` (§3 executors + §5 verification +
legend "through Milestone 7"), `windows-agent/docs/WALKTHROUGH.md` (M7 ✅, test
list, suite count 186), `windows-agent/CHANGES.md` (M7 entry at the top), and
`windows-agent/README.md` (M7 row → done). This HANDOFF is updated to match.

Do this next:
1. **Commit M7** (user commits; hand PowerShell commands). Small commits:
   (a) executor + verifier + `__init__.py` exports + `requirements.txt`,
   (b) `tests/test_presentation_ops.py`, (c) `tools/manual_presentation_test.py`,
   (d) docs (ACTION_REFERENCE + ARCHITECTURE + WALKTHROUGH + CHANGES + README).
   NOTE: unknown whether the user already ran these — check `git log` first.
2. **M9 & M10 are DEFERRED** (user decision, MVP-first). The partial M9 attempt
   was reverted: 2 orphan files deleted, shared files untouched, suite still 186.
   An empty `windows_agent/executors/desktop/` folder may remain — harmless.
3. **M12 IMPLEMENTED & VERIFIED — awaiting user commit (NOT committed yet).**
   Deterministic policy + confirmation engine (module safety core; MVP-critical).
   Full suite **259 passed** (186 baseline + 73 new); no new pip deps (pure
   stdlib). Scope delivered: `DeterministicPolicy` (risk classified purely from
   action type/params, matching the per-action risk in `docs/ACTION_REFERENCE.md`;
   ALLOW/CONFIRM/CLARIFY/DENY with stable `rule_id`s), single-use confirmation
   tokens bound to `action_hash` (reuse / mutated-action / expiry all rejected =
   the injection defense), and backward-compatible dispatcher gating (ALLOW path
   unchanged so the prior tests stay green; DENY/CLARIFY/CONFIRM don't execute; a
   valid `confirmation_token` on re-dispatch lets a consequential action run).
   - **New files:** `windows_agent/policy/deterministic.py`,
     `windows_agent/policy/confirmation.py`, `tests/test_policy.py`,
     `tests/test_confirmation.py`, `tests/test_policy_pipeline.py`,
     `tools/manual_policy_test.py`.
   - **Edited:** `windows_agent/policy/base.py` (fail-closed
     `validate_confirmation` seam), `windows_agent/policy/mock.py` (re-exports
     `action_hash`; mocks KEPT), `windows_agent/policy/__init__.py` (exports), and
     `windows_agent/execution/dispatcher.py` (confirmation gating). Mocks
     (`AllowAllPolicy`/`ConfigurablePolicy`) retained.
   - **M12 docs pass is COMPLETE** (this pass): `docs/ACTION_REFERENCE.md` (new §3
     Policy and confirmation; sections renumbered; catalogue unchanged),
     `docs/ARCHITECTURE.md` (§7 rewritten, [Implemented — M12]),
     `windows-agent/docs/WALKTHROUGH.md` (M12 ✅ + tests + count 259),
     `windows-agent/CHANGES.md` (M12 top entry), `windows-agent/README.md` (M12 row
     ✅), `windows-agent/docs/QUIZ_NOTES.md` (safety Q&A + refreshed caveat), and
     this HANDOFF.
   - **Still TODO:** the user commits M12. Suggested split (hand these PowerShell
     commands to the user; **do not run them yourself** — nothing is committed):

```powershell
cd "G:\interviews\deutche telekom\PS2"
# (a) policy core
git add windows-agent/windows_agent/policy/confirmation.py windows-agent/windows_agent/policy/deterministic.py windows-agent/windows_agent/policy/base.py windows-agent/windows_agent/policy/mock.py windows-agent/windows_agent/policy/__init__.py
git commit -m "M12A: deterministic policy + confirmation tokens" -m "DeterministicPolicy classifies risk from action type/params -> ALLOW/CONFIRM/CLARIFY/DENY with stable rule_ids; ConfirmationStore + action_hash mint single-use, TTL-bound, action-bound tokens (reuse/expiry/mutated-action all fail closed). Mocks retained."
# (b) dispatcher gating
git add windows-agent/windows_agent/execution/dispatcher.py
git commit -m "M12B: dispatcher confirmation gating" -m "dispatch(action, *, confirmation_token=None): CONFIRM runs only with a valid single-use token bound to the exact action (confirmation_accepted); else needs_confirmation (confirmation_rejected then policy_confirmation_required). DENY/CLARIFY never execute; ALLOW path unchanged."
# (c) tests
git add windows-agent/tests/test_policy.py windows-agent/tests/test_confirmation.py windows-agent/tests/test_policy_pipeline.py
git commit -m "M12C: policy + confirmation + pipeline tests" -m "Risk/outcome/rule_id per family, determinism, content-independence/injection cases, token lifecycle, and end-to-end dispatcher gating. Full suite 259 passed."
# (d) manual tester
git add windows-agent/tools/manual_policy_test.py
git commit -m "M12: interactive policy/confirmation manual tester" -m "Drives the real DeterministicPolicy; shows decision/rule_id/token/audit; confirm re-submits with the token; tamper demonstrates the anti-injection rejection."
# (e) docs
git add docs/ACTION_REFERENCE.md docs/ARCHITECTURE.md windows-agent/docs/WALKTHROUGH.md windows-agent/docs/QUIZ_NOTES.md windows-agent/CHANGES.md windows-agent/README.md HANDOFF.md
git commit -m "M12: docs pass (policy + confirmation)" -m "ACTION_REFERENCE new policy/confirmation section; ARCHITECTURE §7 Implemented-M12; WALKTHROUGH/README/CHANGES/QUIZ_NOTES/HANDOFF updated; suite 259."
```

   Verify first (from `windows-agent/`): `& "G:\interviews\deutche telekom\PS2\.venv\Scripts\python.exe" -m pytest -q` → expect **259 passed**; then smoke-test `tools/manual_policy_test.py` (a `file.delete` returns `needs_confirmation` until a valid token is supplied; `tamper` is rejected).

For reference, the commands used to verify M7 were:
```powershell
& "G:\interviews\deutche telekom\PS2\.venv\Scripts\python.exe" -m pip install python-pptx
cd "G:\interviews\deutche telekom\PS2\windows-agent"
& "G:\interviews\deutche telekom\PS2\.venv\Scripts\python.exe" -m pytest -q
"sample`nslides sample.pptx`nread sample.pptx`nfind sample.pptx old`nreplace sample.pptx old new`nread sample.pptx`nquit`n" | & "G:\interviews\deutche telekom\PS2\.venv\Scripts\python.exe" tools\manual_presentation_test.py
```

### NEXT MILESTONE — M13: pause / resume / cancel / correction (START IN A FRESH CONTEXT)

The user wants M13 started with a cleared context. This is the full, self-contained
scope so a new agent can begin without re-deriving anything. **M13 is ours and
MVP-critical** — it delivers required capability #4 ("Support pause, resume,
correction, cancellation, and interruption") and the "user stays in control"
guarantee. Baseline before starting: full suite **259 passed** (run from inside
`windows-agent/`).

**What already exists (DO NOT rebuild):**
- `execution/context.py` → `ExecutionContext`: threadsafe (`RLock`)
  `cancel() / pause() / resume() / is_cancelled() / is_paused()`. Designed so a
  control thread (voice/UI) flips signals while the async pipeline reads them.
- Dispatcher checks `context.is_cancelled()` **once, at the very start** (step 2) →
  returns `ActionStatus.CANCELLED` + emits `ACTION_CANCELLED`.
- Enums present: `ActionStatus.CANCELLED`, `ErrorCode.CANCELLED`,
  `AuditEventType.ACTION_CANCELLED`. Already-reserved-but-UNEMITTED event types:
  `TASK_PAUSED`, `TASK_RESUMED`, `TASK_CORRECTED`, `TASK_CANCELLED`,
  `CONFIRMATION_EXPIRED`.

**Gaps to close (the actual M13 work):**
1. **Cancellation is only checked at start.** Add a second cancel check AFTER the
   policy/confirmation gate and BEFORE the executor runs (dispatcher step 6), so a
   cancel arriving during a confirmation wait (or between propose and re-dispatch)
   prevents the side effect. This is the important safety gap. Consider a further
   check before verification. Each new checkpoint returns `CANCELLED` + emits an
   event.
2. **Pause is never honored.** The dispatcher ignores `is_paused()`. Define
   per-action pause semantics. **Recommended:** keep the dispatcher non-blocking —
   on `is_paused()` at the action boundary, do NOT start the action; return a new
   deferred status and let the planner hold + re-dispatch on resume. This needs a
   new `ActionStatus.PAUSED` (and likely `ErrorCode.PAUSED`) + a pause event.
   (Alternative: block/await until resumed — rejected: it holds the event loop and
   entangles cancellation. Document whichever you choose and why.)
3. **Emit control audit events.** We own **action-level** events (LLM derives
   task-level state). Recommend adding action-level `ACTION_PAUSED` / `ACTION_RESUMED`
   to mirror the existing `ACTION_CANCELLED`, rather than emitting the `TASK_*`
   ones (those read as planner/task-scope). Keep the naming consistent.
4. **Correction (tie-in with M12).** Re-planning itself is the planner's job (M14).
   Our part: make abandoning a queued/in-flight action clean via the cancel path,
   AND **invalidate any outstanding confirmation token for a superseded action** so
   a stale confirmation can never authorize corrected/abandoned work. Add
   `ConfirmationStore.invalidate(action)` / `invalidate_all()` (or document why
   single-use + TTL already suffices) and emit a correction/superseded event.
5. **Interruption** is already supported at the primitive level (threadsafe context
   flipped externally). M13 just needs the dispatcher to READ the signals at the new
   boundaries (covered by 1–2). Add a test that flips a signal from another thread
   mid-dispatch.

**Determinism:** all control checks are deterministic reads of `ExecutionContext`
at fixed pipeline boundaries — no LLM involvement. Same signals → same outcome.

**Deliverables (follow the milestone rhythm in §3):**
- `execution/dispatcher.py`: extra cancel checkpoint(s) + pause handling + control
  events.
- `contracts/enums.py`: `ActionStatus.PAUSED` (+ `ErrorCode.PAUSED`) and, if chosen,
  action-level pause/resume event types in `contracts/audit.py`.
- `policy/confirmation.py`: `invalidate*` for correction (if chosen).
- Tests (`tests/test_control.py`, or extend `test_pipeline.py`): cancel-before-start
  (exists), **cancel-after-confirm-before-execute (new)**, pause-defers,
  resume-proceeds, mid-dispatch interruption from another thread, corrected-action
  invalidates the old token. Keep the existing 259 green.
- Manual tester `tools/manual_control_test.py`: demo pause → resume → cancel and a
  correction, printing status + audit trail (uses `AllowAllPolicy`, except where the
  correction/token demo needs `DeterministicPolicy`).
- Docs: `docs/ARCHITECTURE.md` §6 "user-interruption handling" → `[Implemented — M13]`;
  `windows-agent/docs/WALKTHROUGH.md` (M13 ✅ + suite count); `windows-agent/CHANGES.md`
  (M13 entry); `windows-agent/README.md` (M13 row); `windows-agent/docs/QUIZ_NOTES.md`
  (interruption Q&A); `docs/ACTION_REFERENCE.md` only if a new status becomes
  planner-visible; and this HANDOFF.

**Boundary — do NOT build:** `TaskState` persistence, resume-from-disk, and the
decision of WHEN to pause/cancel (those are planner/UI M14 + teammate audit M11).
We expose only the mechanism, statuses, and events.

---

## 9. Reference docs in the repo

- `docs/ACTION_REFERENCE.md` — **canonical** planner action contract (repo-root).
- `docs/ARCHITECTURE.md` — full architecture narrative (repo-root).
- `docs/DEVELOPMENT_PLAN.md`, `docs/Voice_Controlled_Agent_Shared_Architecture_v1_1.docx` — team umbrella specs (treat as aspirational; reconcile via translation, not mass renames).
- `windows-agent/docs/WALKTHROUGH.md` — module tour + milestone map.
- `windows-agent/docs/QUIZ_NOTES.md` — Q&A study notes (has one stale "M0 only" caveat worth refreshing).
- `windows-agent/CHANGES.md` — per-milestone changelog (doubles as commit bodies).
- `windows-agent/README.md` — module overview.

Note: `ARCHITECTURE.md` and `ACTION_REFERENCE.md` live at **repo-root `docs/`**
(moved out of `windows-agent/docs/`); `WALKTHROUGH.md` and `QUIZ_NOTES.md` remain
in `windows-agent/docs/`.
