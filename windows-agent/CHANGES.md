# Changes

A per-milestone / per-commit changelog. Newest first. Each entry mirrors what a
single milestone commit contains, so it can double as the commit body.

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
