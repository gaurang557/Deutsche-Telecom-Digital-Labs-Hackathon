# Changes

A per-milestone / per-commit changelog. Newest first. Each entry mirrors what a
single milestone commit contains, so it can double as the commit body.

---

## Milestone 6 — Document (Word) Executor + replace_text verifier

**Summary:** Added the `.docx` document capability — three read-only
`document.*` actions plus the headline **`document.replace_text`**, which
corrects a document **while preserving its formatting** (a project-brief
requirement). All backed by **python-docx** (a structured `.docx` API, chosen
over GUI scraping for reliability — see ARCHITECTURE §3/§9; the pip package is
`python-docx`, the import is `docx`). Blocking python-docx load/save runs off
the event loop via `asyncio.to_thread`, mirroring `file_ops`/`pdf_ops`/
`spreadsheet_ops`. The read actions are `RiskLevel.NONE` (no side effects) so
verification is **SKIPPED**; `replace_text` is the verified one — an independent
verifier reopens the OUTPUT document and re-scans its text (the replacement is
present at least the expected number of times, and the old text is gone when the
correction removes it).

**Formatting-preservation approach + limitation:** `.docx` stores a paragraph as
a sequence of *runs*, each with its own formatting. A match **within a single
run** is replaced in place, so that run's formatting (bold/italic/font/…) is
preserved exactly (the common case, asserted in tests). A match **spanning
multiple runs** falls back to a paragraph-level rebuild that writes the result
into the first run and clears the rest — **collapsing that span to the first
run's formatting** (an accepted, documented M6 limitation; a faithful cross-run
edit would require run splitting/merging at XML level).

**Added**
- `executors/document_ops.py` — `DocumentExecutor` handling
  `document.read_text` (non-empty body paragraphs joined with "\n", bounded to
  `_DEFAULT_TEXT_CHAR_CAP` = 20 000 chars, `max_chars` override, `truncated`
  flag), `document.get_metadata` (`core_properties`; datetimes → ISO strings,
  empty strings → null), `document.find` (per-paragraph case-sensitive substring
  counts, bounded to `_DEFAULT_SEARCH_RESULTS` = 100 matching paragraphs), and
  `document.replace_text`. `replace_text` replaces across **body paragraphs,
  table cells, and section headers/footers**, honours an optional positive
  `count` limit, **fails closed with `text_not_found`** when `find` is absent
  (0 replacements reported as an error, nothing written), and writes to a NEW
  file via `save_as` (original untouched) or edits **in place** otherwise; a
  `save_as` that would clobber a different existing file fails with
  `output_exists` unless `overwrite=true`. Structured `_err` returns;
  `_normalize_meta`; `DOCUMENT_ACTION_TYPES`; `register_document_executor`.
  Fails closed on missing file, non-`.docx`/unparseable input, empty `find`.
- `verification/document_verifiers.py` — `DocumentReplaceTextVerifier`:
  independently reopens `output_path` from evidence and re-scans the same places
  the executor edits; PASS iff `replace` occurs ≥ `replacements` times AND (when
  `find != replace` and `find` is not a substring of `replace`) `find` is gone.
  `DOCUMENT_VERIFIERS`; `register_document_verifiers`.
- `tests/test_document_ops.py` — 22 tests (read units incl. text
  bounding/truncation, metadata round-trip, per-paragraph find + empty-query
  error; replace_text single-run **formatting preservation**, multi-occurrence
  across tables, `count` limit, `text_not_found`, empty `find`, `save_as` leaves
  original untouched, in-place edit, `save_as` clobber guard, cross-run fallback;
  verifier PASS + two FAIL cases; error paths; and two end-to-end Dispatcher
  tests: a verified `replace_text` PASSED and a read SKIPPED). `.docx` fixtures
  are built on the fly with python-docx (no committed binaries).
- `tools/manual_document_test.py` — interactive CLI exercising `document.*`
  through the full pipeline against a gitignored `sandbox/`, with a `sample`
  generator (heading + bold text + table + core properties) plus
  `read`/`meta`/`find`/`replace` (in place) / `replaceas` (`save_as`) / `ls`
  commands.

**Changed**
- `executors/__init__.py`, `verification/__init__.py` — export the new executor,
  verifier, `DOCUMENT_ACTION_TYPES`/`DOCUMENT_VERIFIERS`, and register helpers;
  docstrings mention the new modules.
- `requirements.txt` — add `python-docx` (imported as `docx`).
- `docs/ACTION_REFERENCE.md` — new `document.*` sections (read-only params/
  evidence; `replace_text` params/evidence/verification/scope/formatting notes),
  document-specific error codes, and roadmap/status updated to M6.
- `docs/ARCHITECTURE.md` — §3 marks DOCX read+`replace_text` Implemented (M6) via
  python-docx; §5 marks `document.replace_text` verification Implemented (M6) and
  lists the read-only `document.*` actions as SKIPPED; legend now "through
  Milestone 6".
- `docs/WALKTHROUGH.md` — milestone map split: M6 docx ✅ (M7–M8 pptx + doc→pptx
  still planned); `test_document_ops.py` added; suite count updated.

**Risk notes:** document reads = `NONE`; `document.replace_text` = `HIGH` when it
edits **in place** (overwrites the original), `MEDIUM` when `save_as` writes a
new file. (Risk is documentation/policy here; the executor never sets it — the
real policy lands in M12.)

**Tests:** `pytest -q` → **149 passed** (127 baseline + 22 new).

---

## Milestone 4 — Spreadsheet Executor + write verifier

**Summary:** Added the first *modifying* structured-file capability and, with
it, **verification returns**: four read-only `spreadsheet.*` actions plus a
verified `spreadsheet.write_cell`, all backed by openpyxl (a structured `.xlsx`
API, chosen over GUI scraping for reliability — see ARCHITECTURE §3/§9). Blocking
openpyxl load/save runs off the event loop via `asyncio.to_thread`, mirroring
`file_ops`/`pdf_ops`. The read actions are `RiskLevel.NONE` (no side effects) so
verification is **SKIPPED**; `write_cell` is the verified one — an independent
verifier reopens the workbook and re-reads the cell (expected == observed).

**Added**
- `executors/spreadsheet_ops.py` — `SpreadsheetExecutor` handling
  `spreadsheet.list_sheets`, `spreadsheet.dimensions`, `spreadsheet.read_cell`,
  `spreadsheet.read_range` (bounded to `_RANGE_CELL_CAP` = 10 000 cells, whole
  rows clipped with `truncated=true`), and `spreadsheet.write_cell`; structured
  `_err` returns; `_normalize_value` (dates → ISO strings, JSON-serialisable
  primitives); `SPREADSHEET_ACTION_TYPES`; `register_spreadsheet_executor`.
  `write_cell` **creates the workbook if missing** (`created=true`), **fails
  closed on a missing named sheet in an existing workbook** (`sheet_not_found`,
  never silently creates), and **refuses to clobber a non-empty cell** unless
  `overwrite=true` (`cell_occupied`, mirroring `file.write_text`); on success it
  reports `{sheet, cell, value, previous, created, overwrote}` and keeps the
  written value's natural type. Fails closed on missing file (reads), non-`.xlsx`
  target, malformed cell/range refs; workbooks always closed via try/finally.
  **data_only choice:** reads use `data_only=True` (cached values; an
  uncalculated formula reads as `None`), writes use `data_only=False` (preserve
  formulas in other cells).
- `verification/spreadsheet_verifiers.py` — `SpreadsheetWriteCellVerifier`
  mirroring `FileWriteVerifier`: independently reopens the workbook
  (`data_only=False`) and re-reads the target cell, PASS iff observed equals the
  intended written value. **Number-vs-string:** two numbers compare numerically
  (so int `42` vs float `42.0` PASSes), else normalised equality.
  `SPREADSHEET_VERIFIERS`; `register_spreadsheet_verifiers`.
- `tests/test_spreadsheet_ops.py` — 23 tests (executor units for all five
  actions, read-range bounding/truncation, the overwrite guard, verifier
  PASS/FAIL + int-vs-float, error paths, and two end-to-end Dispatcher tests:
  a verified `write_cell` PASSED and a read SKIPPED). `.xlsx` fixtures are built
  on the fly with openpyxl (no committed binaries).
- `tools/manual_spreadsheet_test.py` — interactive CLI exercising
  `spreadsheet.*` through the full pipeline against a gitignored `sandbox/`, with
  a `sample` generator plus `sheets`/`dims`/`readcell`/`readrange`/`writecell`
  (+ `writecellover`) commands.

**Changed**
- `executors/__init__.py`, `verification/__init__.py` — export the new executor,
  verifier, `SPREADSHEET_ACTION_TYPES`/`SPREADSHEET_VERIFIERS`, and register
  helpers; docstrings mention the new modules.
- `requirements.txt` — add `openpyxl`.
- `docs/ACTION_REFERENCE.md` — new `spreadsheet.*` section (params/evidence/
  verification/error codes; risk `NONE` for reads, `MEDIUM`→`HIGH` on overwrite
  for `write_cell`); removed `spreadsheet.*` from the roadmap.
- `docs/ARCHITECTURE.md` — §3 marks XLSX read+write Implemented (M4) via openpyxl;
  §5 marks `spreadsheet.write_cell` verification Implemented (M4); legend now
  "through Milestone 4".
- `docs/WALKTHROUGH.md` — M4 row ✅; `test_spreadsheet_ops.py` added; suite count
  updated.

**Risk notes:** spreadsheet reads = `NONE`; `spreadsheet.write_cell` = `MEDIUM`,
escalating to `HIGH` when it overwrites an existing value. (Risk is
documentation/policy here; the executor never sets it — the real policy lands in
M12.)

**Tests:** `pytest -q` → **127 passed** (104 baseline + 23 new).

---

## Milestone 3 — PDF Executor (read-only)

**Summary:** Added the first document-reading capability: four read-only `pdf.*`
actions backed by PyMuPDF (a structured PDF API, chosen over GUI scraping for
reliability — see ARCHITECTURE §3/§9). Blocking PyMuPDF parsing runs off the
event loop via `asyncio.to_thread`, mirroring `file_ops`. Every action is
`RiskLevel.NONE` (no side effects), so **no verifier is registered and
verification is SKIPPED** — verification exists only for modifying actions.
Extracted text and search matches are bounded so a large PDF can never bloat
evidence.

**Added**
- `executors/pdf_ops.py` — `PdfExecutor` handling `pdf.page_count`,
  `pdf.get_metadata`, `pdf.read_text` (single page or inclusive 0-based range),
  and `pdf.search` (per-page hit counts via `page.search_for`); structured
  `_err` returns; module-level bounding caps (`_DEFAULT_TEXT_CHAR_CAP`,
  `_DEFAULT_SEARCH_RESULTS`); `PDF_ACTION_TYPES`; `register_pdf_executor`. Fails
  closed on missing file, non-PDF/unparseable input, password-protected PDFs
  (never prompts), out-of-range page indices, and empty search queries; the
  `fitz` document is always closed via try/finally.
- `tests/test_pdf_ops.py` — 19 tests (executor units for the four actions,
  bounding/cap enforcement, error paths, plus end-to-end via the Dispatcher
  asserting SUCCESS with bounded evidence and verification SKIPPED). Test PDFs
  are built on the fly with PyMuPDF (no committed binary fixtures).

**Changed**
- `executors/__init__.py` — export `PdfExecutor`, `PDF_ACTION_TYPES`,
  `register_pdf_executor`; docstring mentions `pdf_ops.py`.
- `requirements.txt` — add `PyMuPDF` (imported as `fitz`).
- `docs/ACTION_REFERENCE.md` — new `pdf.*` section (params/evidence/error codes,
  risk `NONE`); removed `pdf.*` from the roadmap.
- `docs/ARCHITECTURE.md` — §3 marks PDF reading Implemented (M3) via PyMuPDF; §5
  notes read-only actions need no verifier.
- `docs/WALKTHROUGH.md` — M3 row ✅; `test_pdf_ops.py` added to the test list.

**Tests:** `pytest -q` → **104 passed** (85 baseline + 19 new).

---

## Docs — move shared deliverables to repo-root docs/

**Summary:** Moved `ARCHITECTURE.md` and `ACTION_REFERENCE.md` out of the module
into the team-shared repo-root `docs/` (via `git mv`, history preserved), so the
architecture deliverable and the LLM-facing action reference sit alongside the
other shared team docs. `WALKTHROUGH.md` and `QUIZ_NOTES.md` stay module-local.
Updated cross-references in `README.md`, `docs/WALKTHROUGH.md`, and
`docs/QUIZ_NOTES.md`.

---

## Contract reconciliation + native audit-log reader

**Summary:** Aligned our contracts with the shared team `agent/models.py` where
it was safe to, and gave the LLM a native, read-only view of our audit events.

**Changed**
- `contracts/enums.py` — `RiskLevel` now `NONE, LOW, MEDIUM, HIGH, CONSEQUENTIAL, FORBIDDEN`
  (shared vocabulary + our `FORBIDDEN`). Risk is set by our deterministic policy and
  surfaced on `PolicyDecision.risk_level`; the LLM only ingests it (still off the
  `Action`; `extra="forbid"` retained).
- `policy/mock.py` — default `RiskLevel.READ` → `RiskLevel.NONE`.
- `docs/ACTION_REFERENCE.md` — risk groupings + enum list updated to the new vocabulary.

**Added**
- `audit/query.py` — `AuditLogReader` (fetch native `AuditEvent`s as JSON-serialisable
  dicts, filterable by task/action/event-type/time range/limit) + `redact()` seam
  (no-op until M11). We emit action-level events; the LLM queries/translates them.
- `tests/test_audit_query.py` — reader tests.

**Deliberately unchanged** (handled by the translation layer / LLM, per team call):
field-name renames, timestamp formats, `AuditEvent.request_id` vs `task_id`,
coarse event vocabulary, structured `ActionError` vs string, `VerificationResult`
mapping, `ActionStatus` values, and our executor names.

**Tests:** `pytest -q` → **85 passed.**

---

## Milestone 2 — File Operations Executor + Verifiers

**Summary:** First real capability on the pipeline: eight `file.*` actions with
independent re-observation verifiers. Blocking filesystem I/O runs off the event
loop via `asyncio.to_thread`; reads/listings are capped; SHA256 hashes are
recorded as evidence for verification.

**Added**
- `executors/file_ops.py` — `FileExecutor` handling `file.exists`, `file.list`,
  `file.read_text`, `file.copy`, `file.move`, `file.write_text`, `file.mkdir`,
  `file.delete`; `sha256_file` helper; structured `_err` returns; `register_file_executor`.
- `verification/file_verifiers.py` — `FileCopyVerifier` (source/dest hash match),
  `FileMoveVerifier` (source absent + dest present + hash matches pre-move evidence),
  `FileWriteVerifier`, `FileMkdirVerifier`, `FileDeleteVerifier`; `register_file_verifiers`.
- `tests/test_file_ops.py` — 21 tests (executor units, verifier units, end-to-end via dispatcher).
- `tools/manual_file_test.py` — interactive CLI that exercises `file.*` through the
  full pipeline against a `sandbox/` dir (sandbox is gitignored).
- `docs/ACTION_REFERENCE.md` — LLM-facing reference for the `file.*` actions.

**Changed**
- `executors/__init__.py`, `verification/__init__.py` — export the new
  executor/verifiers + register helpers.

**Tests:** `file.*` unit + end-to-end all green.

---

## Housekeeping — self-contained module layout

**Summary:** Moved the entire project into a single self-contained folder,
`windows-agent/`, so it lives as an isolated entity inside the shared source
tree and neither affects nor is affected by other teammates' work.

- Moved `windows_agent/` (package), `tests/`, `README.md`, `CHANGES.md`,
  `requirements.txt`, `pytest.ini`, and `docs/{ARCHITECTURE,WALKTHROUGH,QUIZ_NOTES}.md`
  under `windows-agent/`.
- Left shared/pre-existing files at the source root untouched
  (`docs/Starter-B.md`, `docs/DEVELOPMENT_PLAN.md`, root `.gitignore`, `.git`).
- Added `windows-agent/.gitignore` so build/test artifacts stay inside the module.
- Docs: added `docs/WALKTHROUGH.md`; README now notes the module is self-contained
  and all commands run from inside `windows-agent/`.

**Tests:** `pytest -q` from `windows-agent/` → **50 passed**.

---

## Milestone 1 — Execution Pipeline Foundation

**Summary:** Turned the Milestone 0 interfaces into a single, safe execution
pipeline: `Action → schema validation → registry → policy gateway → executor →
verification → ActionResult`, with structured audit events emitted around every
stage. Policy and audit use interfaces + mocks (real engine/persistence arrive
in M11/M12).

**Added**
- `contracts/policy.py` — `PolicyDecision` (deterministic authorization verdict; `action_hash` binds confirmations to the exact action).
- `contracts/audit.py` — `AuditEvent` + full `AuditEventType` vocabulary (M1 events used; later events reserved).
- `policy/` — `Policy` interface; `AllowAllPolicy`, `ConfigurablePolicy`; internal `action_hash()` (not planner-visible).
- `verification/` — `Verifier` interface; `VerificationRegistry` (`register_verifier/get_verifier/has_verifier/verify_action`; unknown → SKIPPED).
- `audit/` — `AuditSink` interface; `InMemoryAuditSink`, `NullAuditSink`.
- `execution/context.py` — `ExecutionContext` (`is_cancelled/is_paused/cancel/pause/resume`), thread-safe.
- Tests: `test_registry.py`, `test_verification.py`, `test_pipeline.py`, `test_audit_events.py` (24 new tests).

**Changed**
- `contracts/enums.py` — added `PolicyOutcome`, `RiskLevel`; extended `ErrorCode` (`POLICY_DENIED`, `CONFIRMATION_REQUIRED`, `CLARIFICATION_REQUIRED`, `VERIFICATION_FAILED`, `CANCELLED`).
- `contracts/__init__.py` — export `PolicyOutcome`, `RiskLevel`, `PolicyDecision`, `AuditEvent`, `AuditEventType`.
- `execution/registry.py` — roadmap-named methods (`register_action`, `unregister_action`, `get_action_handler`, `has_action`, `list_registered_actions`); M0 names kept as aliases.
- `execution/dispatcher.py` — full pipeline: `dispatch` (now accepts `Action | dict`), `execute_authorized_action`, `build_action_result`; policy gateway, verification gating, audit emission; fail-closed on invalid/unknown/exception.
- `execution/__init__.py` — export `ExecutionContext`.
- `tests/test_dispatcher.py` — dispatcher helper now supplies `AllowAllPolicy()` (constructor requires an explicit policy; assertions unchanged).

**Behaviour / interface notes**
- `Dispatcher(...)` now **requires an explicit `policy`** (fail-closed; no self-authorization).
- Verification runs only after a successful execution; a `FAILED` verification forces the overall `ActionResult` to `FAILED`.
- Evidence remains size-bounded before leaving the dispatcher.

**Not yet implemented (by design):** real deterministic policy (M12), SQLite
audit persistence + redaction (M11), pause/resume semantics (M13).

**Docs:** added `docs/WALKTHROUGH.md`, `CHANGES.md`; `README.md` and
`docs/ARCHITECTURE.md` already describe the intended full design.

**Tests:** `pytest -q` — 24 new + all M0 tests (regression). **50 passed.**

---

## Milestone 0 — Shared Contracts & Execution Skeleton

**Summary:** Established the execution contract and a safe single execution path,
with mock executors and tests. No desktop/LLM/voice/policy yet.

**Added**
- `contracts/` — `Action` (with `extra="forbid"` so authority fields are rejected), `ActionError`, `VerificationResult`, `ExecutorResult`, `ActionResult`, and `enums.py` (`ActionStatus`, `VerificationStatus`, `ErrorCode`).
- `executors/base.py` — `BaseExecutor` (async `execute`).
- `executors/common/mock.py` — `EchoExecutor`, `FailingExecutor`, `RaisingExecutor`.
- `execution/registry.py` — `ActionRegistry`.
- `execution/dispatcher.py` — `Dispatcher` (lookup → execute → bounded `ActionResult`; fail-closed).
- Tests: `test_contracts.py`, `test_dispatcher.py`.
- Project meta: `requirements.txt`, `pytest.ini` (`asyncio_mode=auto`, `pythonpath=.`).
- Docs: `README.md`, `docs/ARCHITECTURE.md`, `docs/QUIZ_NOTES.md`.

**Key decisions**
- Package name `windows_agent`; async-first execution path.
- Enums own their values; a translation layer bridges any component mismatch.
- Evidence is bounded; unknown actions and executor exceptions fail closed.
