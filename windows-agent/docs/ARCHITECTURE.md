# Architecture Description

This document explains the design of `windows_agent` and the reasoning behind
each decision. It addresses every topic required by the deliverable. Where a
capability is not yet implemented, it is marked **[Planned]** and the design is
described so the intent is clear and reviewable.

Legend: **[Implemented]** = code exists today (Milestone 0). **[Planned]** =
designed, lands in a later milestone.

---

## 0. Design philosophy (read this first)

The system is built around one non-negotiable invariant:

> **An LLM may propose actions; only deterministic code may authorize them.**

Everything else — the schemas, the dispatcher, the registry, the milestone
ordering — exists to make that invariant *structurally true* rather than a
matter of good behaviour. Concretely:

- The `Action` schema **cannot carry** risk/permission/trust/confirmation/
  authorization fields (`extra="forbid"`), so a model cannot smuggle authority
  into the pipeline.
- Authorization is a **separate deterministic stage** (the Policy Engine) that
  the LLM never touches.
- Content read from PDFs/webpages/documents/UIs is **untrusted data**, never
  commands.

The required end-to-end flow:

```
User voice/text → TaskRequest → Planner → Action
  → deterministic Policy Engine → allow / deny / confirm / clarify
  → Dispatcher → Executor → Verification → ActionResult
  → Planner / Conversation layer
(every stage also emits structured audit events)
```

---

## 1. Speech-processing approach  **[Planned — M5]**

- **ASR:** `faster-whisper` (open-weight Whisper) with local inference; push-to-
  talk rather than always-on wake-word for reliability and privacy.
- **TTS:** a local open-weight engine (e.g. Piper) for spoken responses and
  confirmation prompts.
- **Design rule:** the speech layer only ever produces a `TaskRequest`
  (text + metadata). It has **no authority**. Control intents (pause, resume,
  cancel, correct) are detected before normal planning so the user can always
  interrupt.
- **Transcription errors** are treated as ambiguity: low confidence or
  safety-relevant uncertainty triggers a clarification question rather than a
  guess.

## 2. Planning and execution architecture

### Planning **[Planned — M5]**
- A local open-weight LLM turns a `TaskRequest` into a **plan** of `Action`s.
- The LLM is constrained to an **allow-listed semantic vocabulary** (~55
  actions across file/pdf/spreadsheet/document/presentation/desktop/browser).
- The LLM output is validated against the `Action` schema; malformed or unknown
  actions are rejected. The planner proposes; it never authorizes, sets risk,
  or approves confirmations.

### Execution **[Implemented — M0]**
The execution path is the part built today, and it is deliberately the
backbone everything else plugs into:

- **`Action`** (`contracts/action.py`): the typed proposal — `action_id`,
  `task_id`, `sequence`, `type`, `target`, `parameters`, `expected_result`,
  `reason`. No authority fields (enforced by `extra="forbid"`).
- **`ActionRegistry`** (`execution/registry.py`): maps an action `type` string
  to the executor that handles it. This replaces one giant `execute_action()`
  full of conditionals; adding a capability = registering a handler. Unknown
  types return `None` so the caller can fail safely. Duplicate registration
  raises (guards against accidental clobbering).
- **`BaseExecutor`** (`executors/base.py`): the async contract every executor
  implements — `async def execute(action) -> ExecutorResult`. Async-first
  because real executors will do I/O (files, PDF parsing, browser/desktop);
  choosing async now avoids rewriting every executor later.
- **`Dispatcher`** (`execution/dispatcher.py`): the **single execution path**.
  Today: `registry lookup → execute → bound evidence → ActionResult`. It is
  also the one place the deterministic cross-cutting stages will be wired, in
  this exact order:
  `validate → [policy authorize] → [confirmation] → execute → [verification] → [audit]`.
  Having one path means safety/verification/audit can never be bypassed by an
  individual executor.

**Why two result types?**
- `ExecutorResult` (internal): `success`, `evidence`, `side_effects`, `error`.
  What an executor knows.
- `ActionResult` (external): `action_id`, `task_id`, `status`, `evidence`,
  `verification`, `error`. What the planner needs. This separation keeps
  executors ignorant of planner-facing concepts (status enum, verification),
  and lets the dispatcher enforce evidence bounding and attach verification
  centrally.

## 3. Screen and application understanding  **[Planned — M4]**

Executor preference order (most reliable first):
1. **Structured file/application APIs** — PyMuPDF (PDF), openpyxl (XLSX),
   python-docx (DOCX), python-pptx (PPTX). Reading a cell via a library is far
   more reliable than scraping a GUI.
2. **Accessibility / semantic UI automation** — Windows UI Automation via
   `pywinauto` (roles, names, control patterns).
3. **Keyboard shortcuts**.
4. **OCR / screen understanding** (fallback).
5. **Raw coordinate clicking** — last resort only.

Low-level primitives (mouse coordinates, key events, screen capture, OCR
internals, accessibility queries) are **internal only** and never exposed to
the LLM, which sees only the ~55 semantic actions. Screen content and document
text are always **untrusted data**.

## 4. State management  **[Planned — M3]**

- A task state machine tracks status, current step, the plan, and completed-
  step history.
- Supports **pause, resume, cancel, correction/replanning**. No new action
  begins while paused; cancellation stops further actions at a safe boundary;
  completed actions remain recorded when the remaining plan is revised.
- The desktop layer may cache small runtime context (active window/app), but it
  is **revalidated against the real environment before important actions** — a
  window may have closed or focus changed.

## 5. Action verification  **[Planned — M2]**

- **A function returning without an exception is NOT proof of success.** Every
  modifying action must independently **re-observe state**:
  - `file.copy` → destination exists and matches source.
  - `file.move` → destination exists, source absent, contents match.
  - `spreadsheet.write_cell` → reload and re-read the cell; expected == observed.
  - `document.replace_text` → reopen; the replacement is present.
- `expected_result` on the `Action` feeds the verification assertion.
- **Consequential actions are never auto-retried.** Retries are limited and
  logged with evidence.
- The result is a `VerificationResult` (`status`, `method`, `expected`,
  `observed`, `message`) attached to the `ActionResult` by the dispatcher.

## 6. User-interruption handling  **[Planned — M3/M5]**

- Control intents (`pause`/`resume`/`cancel`/`correct`) are recognised by the
  conversation layer and checked **between every action** and **before
  accepting a confirmation**.
- Corrections trigger **replanning** that preserves completed work.
- Because authorization is bound to a specific action (see §7), an interruption
  or change invalidates any pending confirmation.

## 7. Permission and safety model (the core)  **[Planned — M1; enforced structurally in M0]**

- **Deterministic risk classification** into fixed classes:
  `READ`, `NAVIGATE`, `MODIFY`, `CONSEQUENTIAL`, `FORBIDDEN`. Computed by pure
  code from the action type/target/parameters — never by the LLM.
  - READ: pdf/spreadsheet read, list files, inspect controls.
  - NAVIGATE: open/focus app, browser navigation.
  - MODIFY: create/edit a local working file, rename/move within the workspace.
  - CONSEQUENTIAL: delete, overwrite original, submit form, send/publish,
    upload sensitive info → **requires confirmation immediately before execution**.
  - FORBIDDEN: arbitrary shell / PowerShell / CMD / registry / software install /
    any command sourced from a webpage/document/PDF → **always denied**.
- **`PolicyDecision`** carries `outcome` (allow/deny/confirm/clarify),
  `risk_level`, a stable `rule_id`, a human-readable `reason`, an optional
  `confirmation_token`, and an `action_hash`.
- **Confirmation is bound to the exact action** via `action_hash`. Changing the
  action's type, target, or parameters invalidates the prior confirmation, so a
  user can never approve one thing and have another executed.
- **Structural enforcement today (M0):** `Action` uses `extra="forbid"`, so the
  planner/LLM cannot attach `risk`, `permission`, `trust`, `confirmation`, or
  `authorization`. The authority literally has nowhere to live except the
  deterministic Policy Engine. Unknown actions and executor exceptions
  **fail closed** (a structured `FAILED` result, never an unhandled crash).
- **Prompt-injection resistance:** retrieved content is data. An
  `UNTRUSTED_CONTENT_DETECTED` audit event is emitted when injection-like text
  is seen, but such content can never change an authorization outcome.

## 8. Audit  **[Planned — M3]**

- Audit events are produced **centrally around the execution lifecycle** (in
  the dispatcher), not via ad-hoc logging scattered through executors.
- Lifecycle event types include: `TASK_STARTED`, `TRANSCRIPT_RECEIVED`,
  `PLAN_CREATED/REVISED`, `ACTION_PROPOSED`, `POLICY_ALLOWED/DENIED/
  CONFIRMATION_REQUIRED`, `CONFIRMATION_REQUESTED/ACCEPTED/REJECTED/EXPIRED`,
  `ACTION_STARTED/COMPLETED/FAILED/CANCELLED`, `VERIFICATION_STARTED/PASSED/
  FAILED`, `TASK_PAUSED/RESUMED/CORRECTED/CANCELLED`, `TASK_COMPLETED/FAILED`,
  `UNTRUSTED_CONTENT_DETECTED`.
- **Redaction** is central: all audit data passes through one redaction layer
  before persistence. Secrets, passwords, tokens, cookies, and complete
  document contents are never stored.
- Persistence uses **SQLite** (no server; portable, queryable history).
- Evidence is **bounded** everywhere (the dispatcher already caps string and
  collection sizes) so logs and planner context never contain whole
  PDFs/workbooks/DOM trees.

## 9. Model selection  **[Planned]**

All models are open-source / open-weight and run locally:

| Component | Choice | Why |
|-----------|--------|-----|
| ASR | faster-whisper (Whisper) | Accurate, offline, CPU-capable (int8) |
| Planner LLM | small local instruct model (e.g. Qwen2.5 / Llama 3.1 via a local runtime) | Good structured-output reasoning, runs locally, open-weight |
| TTS | Piper (or system voice) | Fast, offline, natural |
| Desktop | Windows UI Automation (`pywinauto`) | Accessibility-first grounding, robust vs pixel matching |
| Browser | Playwright | Semantic DOM/accessibility locators; content kept untrusted |
| Files/office | PyMuPDF, openpyxl, python-docx, python-pptx | Deterministic reads/edits + verifiable |

The LLM is used for **language understanding and proposing plans only** — never
for authorization, risk labelling, verification, or code/command execution.

---

## Important engineering trade-offs

- **LLM proposes, deterministic code authorizes.** Keeps natural-language
  flexibility while making safety reproducible, explainable (`rule_id` +
  `reason`), and testable.
- **Single dispatcher path + action registry** instead of a monolithic
  `execute_action()`. Cross-cutting concerns (policy, confirmation,
  verification, audit) are wired once and cannot be bypassed; new capabilities
  are added by registering a handler.
- **Two result types (internal `ExecutorResult` vs external `ActionResult`).**
  Executors stay ignorant of planner concepts; the dispatcher owns evidence
  bounding and verification attachment.
- **Async-first execution.** Real executors are I/O-bound; committing to async
  now avoids rewriting every executor and the dispatcher later.
- **Bounded evidence by default.** Protects against context bloat, cost, and
  feeding large untrusted blobs to the LLM.
- **Fail-closed everywhere.** Unknown action → `FAILED`; executor exception →
  contained `FAILED`; missing confirmation → no execution; ambiguity → clarify.
- **Structured file APIs over GUI scraping** where possible for reliability,
  with accessibility and then coordinate clicking as graded fallbacks.
- **Windows-first, adapter-isolated.** Planner/policy/state/audit/contracts are
  platform-agnostic; only the desktop adapter is platform-specific, so a macOS
  adapter can be added without touching the core.
- **Milestone-by-milestone delivery.** Each milestone is tested before the next
  begins, so the deterministic execution/safety core is proven before the LLM
  and voice layers are connected.
