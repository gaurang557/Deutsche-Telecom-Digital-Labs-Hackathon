# Architecture Description

This document explains the design of `windows_agent` and the reasoning behind
each decision. It addresses every topic required by the deliverable. Where a
capability is not yet implemented, it is marked **[Planned]** and the design is
described so the intent is clear and reviewable.

Legend: **[Implemented]** = code exists today (through Milestone 6). **[Planned]** =
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
- `ACTION_REFERENCE.md` is the canonical planner-visible runtime vocabulary;
  names from older shared documents are translated at the integration boundary,
  not registered as aliases.
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
  to immutable registration metadata: the action type, executor, and an
  explicit `requires_verification` boolean. Registration requires that boolean
  as a keyword-only argument, so future actions cannot silently default to
  optional verification. This replaces one giant `execute_action()` full of
  conditionals; adding a capability = registering a handler and its
  verification requirement. Unknown types return `None` so the caller can fail
  safely. Duplicate registration raises (guards against accidental clobbering).
- **`BaseExecutor`** (`executors/base.py`): the async contract every executor
  implements — `async def execute(action) -> ExecutorResult`. Async-first
  because real executors will do I/O (files, PDF parsing, browser/desktop);
  choosing async now avoids rewriting every executor later.
- **`Dispatcher`** (`execution/dispatcher.py`): the **single execution path**:
  `validate → registry lookup → policy authorize → required-verifier guard →
  execute → verification → bound ActionResult`, with audit events around each
  stage. Policy remains authoritative and always runs before an executor. After
  ALLOW, an action marked `requires_verification` fails with
  `verifier_missing` before execution if its verifier is absent.
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
1. **Structured file/application APIs** — PyMuPDF (PDF) **[Implemented — M3, read
   only]**, openpyxl (XLSX) **[Implemented — M4, read + write_cell]**, python-docx
   (DOCX) **[Implemented — M6, read + replace_text]**, python-pptx (PPTX)
   **[Planned — M7]**. The former M8 document→presentation workflow is deferred
   to planner/LLM integration in M14: the planner composes M6 and M7 actions
   rather than a hardcoded execution workflow. Reading (or writing) a cell — or
   a PDF page — via a library is far
   more reliable than scraping a GUI. The read-only `pdf.*` actions
   (`pdf.page_count`, `pdf.get_metadata`, `pdf.read_text`, `pdf.search`) live in
   `executors/pdf_ops.py`. The `spreadsheet.*` actions (`list_sheets`,
   `dimensions`, `read_cell`, `read_range` read-only; `write_cell` modifying) live
   in `executors/spreadsheet_ops.py`; reads use `data_only=True` (cached values)
   and writes use `data_only=False` (preserving formulas elsewhere). The
   `document.*` actions (`read_text`, `get_metadata`, `find` read-only;
   `replace_text` modifying) live in `executors/document_ops.py`; `replace_text`
   edits at the **run level** to preserve formatting (a cross-run match falls back
   to the first run's formatting — documented limitation). Extracted text/values
   are bounded and treated as untrusted data.
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

## 5. Action verification  **[Implemented — M2 for file.*, M4 for spreadsheet.write_cell, M6 for document.replace_text; more executors later]**

- **A function returning without an exception is NOT proof of success.** Every
  modifying action must independently **re-observe state**. Implemented today
  (M2, `verification/file_verifiers.py`):
  - `file.copy` → destination exists and its SHA256 matches the source. [Implemented]
  - `file.move` → destination present, source absent, hash matches pre-move evidence. [Implemented]
  - `file.write_text` → re-read; content/length matches what was written. [Implemented]
  - `file.mkdir` → the directory now exists. [Implemented]
  - `file.delete` → the path is gone. [Implemented]
  - `spreadsheet.write_cell` → reload the workbook and re-read the cell; expected == observed (numbers compared numerically). [Implemented — M4]
  - `document.replace_text` → reopen the output document and re-scan its text; the replacement is present at least the expected number of times and (when the correction removes it) the original text is gone. [Implemented — M6]
- **Read-only actions need no verifier.** The `pdf.*` actions added in M3
  (`executors/pdf_ops.py`), the read-only `spreadsheet.*` actions added in M4
  (`spreadsheet.list_sheets`/`dimensions`/`read_cell`/`read_range`), and the
  read-only `document.*` actions added in M6
  (`document.read_text`/`get_metadata`/`find`) are `RiskLevel.NONE` (no side
  effects), so no verifier is registered and the VerificationRegistry correctly
  returns `SKIPPED` — the same treatment as
  `file.exists`/`file.list`/`file.read_text`.
- Verification requirements are deterministic action-registration metadata,
  not planner input and not inferred from risk. A required verifier must be
  present before execution; if it unexpectedly returns `SKIPPED`, the
  dispatcher converts that outcome to `FAILED`.
- Exceptions raised by a verifier or the verification registry after execution
  are contained as a failed `VerificationResult` (including the exception type)
  and emit `verification_failed`; they do not escape the dispatcher.
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

## 7. Permission and safety model (the core)  **[Structural gateway implemented; deterministic policy planned — M12]**

- M12 is mandatory work owned by this execution/safety module, not by the LLM:
  **M12A** implements deterministic risk classification and rules, **M12B**
  validates confirmation tokens bound to the exact `action_hash`, and **M12C**
  integrates the real policy with the dispatcher and adds fail-closed tests.
  The LLM may explain a `PolicyDecision` and collect a user's response; it
  cannot classify risk, authorize an action, or validate confirmation.
- **Deterministic risk classification** into fixed classes (aligned with the
  shared team `RiskLevel` vocabulary, plus our `FORBIDDEN`):
  `NONE`, `LOW`, `MEDIUM`, `HIGH`, `CONSEQUENTIAL`, `FORBIDDEN`. Computed by pure
  code from the action type/target/parameters — **the LLM never sets risk; it
  only ingests the value our policy assigns** (surfaced on `PolicyDecision.risk_level`).
  - NONE: read-only, no side effects (pdf/spreadsheet read, list files,
    `file.exists`/`file.list`/`file.read_text`, inspect controls).
  - LOW: local and reversible (type into an unsaved draft).
  - MEDIUM: creates local state (`file.copy`, `file.write_text`, `file.mkdir`, save new file).
  - HIGH: destructive but local (`file.move`, `file.delete`, overwrite original,
    bulk rename) → **requires confirmation**.
  - CONSEQUENTIAL: leaves the machine (send, submit, publish, purchase) →
    **requires confirmation immediately before execution**.
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

## 8. Audit  **[Action-level emission implemented; M11 externally owned]**

- This module owns complete **action-level** audit event emission around the
  execution lifecycle, bounded/JSON-serialisable details, and compatibility
  with the audit teammate's sink/query contract. Events are produced centrally
  in the dispatcher, not via ad-hoc executor logging.
- Emitted action-level events cover action proposal, policy outcomes,
  execution start/completion/failure/cancellation, and verification
  start/pass/failure/skip. Task aggregation and broader lifecycle records belong
  to the shared audit system.
- M11 implementation and maintenance are external/team-owned. This module will
  not implement SQLite persistence, security redaction, retention, task
  aggregation, query UI, shared-schema translation, or a parallel audit system.
- `AuditLogReader` and its current no-op `redact()` seam are existing
  compatibility scaffolding only. They do not commit this module to maintaining
  duplicate audit infrastructure or performing security redaction. The
  teammate-owned audit boundary must own the required redaction before the final
  demo.
- The LLM may consume only events permitted through the teammate-owned
  redaction/audit boundary. It never decides what is logged and never performs
  security redaction.
- After LLM/shared-audit integration, this module owns integration/E2E tests
  proving action events are delivered, translated and queried as agreed, and
  sensitive data is handled at the teammate-owned redaction/audit boundary.
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
- **Fail-closed everywhere.** Unknown action → `FAILED`; executor/verifier
  exception → contained `FAILED`; required verifier missing → no execution;
  missing confirmation → no execution; ambiguity → clarify.
- **Structured file APIs over GUI scraping** where possible for reliability,
  with accessibility and then coordinate clicking as graded fallbacks.
- **Windows-first, adapter-isolated.** Common executors and
  planner/policy/state/audit contracts remain platform-neutral, while
  Windows-specific UI automation stays behind an adapter. M17 cross-platform/
  macOS readiness has been removed from the active roadmap due to time
  constraints; this boundary does not promise or schedule macOS/Linux
  implementation or testing.
- **Milestone-by-milestone delivery.** Each milestone is tested before the next
  begins, so the deterministic execution/safety core is proven before the LLM
  and voice layers are connected.
