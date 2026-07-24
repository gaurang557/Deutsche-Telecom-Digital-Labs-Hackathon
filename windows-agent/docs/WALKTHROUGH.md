# Code Walkthrough

A guided tour of the codebase. This file explains **how the pieces fit together
and in what order to read them**. The detailed "what/why" for each component
lives in that component's own module docstring and inline comments — this
walkthrough points you there rather than duplicating it.

> If you only have five minutes: read §1 (the invariant) and §3 (the data flow),
> then open `execution/dispatcher.py`.

---

## 1. The one invariant that explains every decision

> **The LLM may PROPOSE actions; only deterministic code may AUTHORIZE them —
> enforced structurally, not by convention.**

Keep this in mind while reading; almost every design choice serves it. Full
reasoning: `../docs/ARCHITECTURE.md` §0 and `docs/QUIZ_NOTES.md`.

## 2. Suggested reading order

Read the files in the same order data flows through them:

1. `windows_agent/contracts/action.py` — the unit of work (`Action`).
2. `windows_agent/contracts/results.py` — `ExecutorResult` (internal) vs `ActionResult` (external).
3. `windows_agent/contracts/policy.py` — `PolicyDecision` (the authorization verdict).
4. `windows_agent/contracts/verification.py` — `VerificationResult`.
5. `windows_agent/contracts/audit.py` — `AuditEvent` / `AuditEventType`.
6. `windows_agent/executors/base.py` — the async executor contract.
7. `windows_agent/execution/registry.py` — action type → executor.
8. `windows_agent/policy/base.py` + `policy/mock.py` — the policy gateway.
9. `windows_agent/verification/registry.py` — action type → verifier.
10. `windows_agent/audit/sink.py` — where audit events go.
11. `windows_agent/execution/dispatcher.py` — the pipeline that orchestrates all of the above. **This is the heart of the system.**

## 3. The end-to-end data flow (Milestone 1)

```
Action (or dict)
   │  Dispatcher.dispatch()                         execution/dispatcher.py
   ├─ schema validation ......... dict → Action; invalid → FAILED(VALIDATION_ERROR)
   ├─ cancellation boundary ..... context.is_cancelled() → CANCELLED (executor never runs)
   ├─ registry lookup ........... registry.get_action_handler(); unknown → FAILED(UNKNOWN_ACTION)
   ├─ policy gateway ............ policy.authorize(action) → PolicyDecision
   │     DENY    → DENIED               (executor NOT called)
   │     CONFIRM → NEEDS_CONFIRMATION   (executor NOT called)
   │     CLARIFY → CLARIFY              (executor NOT called)
   │     ALLOW   → continue
   ├─ execute ................... execute_authorized_action() → ExecutorResult (exceptions contained)
   ├─ verify .................... only if execution succeeded → VerificationResult
   └─ build_action_result() ..... verification FAILED forces overall FAILED
        │
        ▼
   ActionResult  +  ordered AuditEvents emitted around every stage
```

Component responsibilities:

| Concern | Owner | File |
|--------|-------|------|
| What to do | `Action` | `contracts/action.py` |
| Route to code | `ActionRegistry` | `execution/registry.py` |
| May it run? | `Policy` → `PolicyDecision` | `policy/`, `contracts/policy.py` |
| Do it | `BaseExecutor` → `ExecutorResult` | `executors/` |
| Did it really work? | `VerificationRegistry` → `VerificationResult` | `verification/` |
| Report to planner | `ActionResult` | `contracts/results.py` |
| Record everything | `AuditSink` ← `AuditEvent` | `audit/`, `contracts/audit.py` |
| Interruptions | `ExecutionContext` | `execution/context.py` |

## 4. Why the fail-closed guarantees matter

Every "bad" path returns a structured `ActionResult` with `status=FAILED` (or
`DENIED`/`CANCELLED`), never an unhandled exception: invalid input, unknown
action, executor crash. Consequential work cannot run without an `ALLOW`
decision. See the guards in `execution/dispatcher.py`.

## 5. Milestone map (where things are / will be)

| Milestone | Adds | Status |
|-----------|------|--------|
| M0 | contracts, `BaseExecutor`, registry, dispatcher, mocks | ✅ |
| M1 | policy gateway (mock), verification registry, audit sink, execution context, full pipeline | ✅ |
| M2 | `file.*` executor (`executors/file_ops.py`) + verifiers (`verification/file_verifiers.py`) + native audit-log reader (`audit/query.py`) | ✅ |
| M3 | read-only PDF executor (`executors/pdf_ops.py`) | ✅ |
| M4 | spreadsheet executor (`executors/spreadsheet_ops.py`) + write verifier (`verification/spreadsheet_verifiers.py`) | ✅ |
| M5 | first deterministic multi-app workflow (PDF→spreadsheet) | deferred to planner integration / M14 |
| M6 | docx executor (`executors/document_ops.py`) + replace_text verifier (`verification/document_verifiers.py`) | ✅ |
| M7 | presentation executor | planned |
| M8 | document→presentation orchestration | deferred to planner/LLM integration / M14; not a hardcoded execution workflow |
| M9 | Windows desktop adapter (UI Automation) | planned |
| M10 | browser executor (Playwright) | planned |
| M11 | shared audit implementation | external/team-owned; this module guarantees action events + sink/query compatibility and tests the integrated boundary |
| M12 | deterministic policy, risk, and confirmation binding | planned in this execution/safety module |
| M13 | pause/resume/cancel/correction integration | planned |
| M14 | planner/LLM tool integration, including deferred M5/M8 orchestration | planned |
| M15 | end-to-end regression | planned |
| M16 | hardening | planned |
| M17 | cross-platform/macOS readiness | removed from active roadmap due to time constraints |
| M18 | release/demo freeze | planned |

## 6. How to run

```powershell
pip install -r requirements.txt
pytest -q
```

`pytest.ini` sets `asyncio_mode=auto` and `pythonpath=.`. Test files mirror the
milestones: `test_contracts.py`, `test_dispatcher.py` (M0); `test_registry.py`,
`test_verification.py`, `test_pipeline.py`, `test_audit_events.py` (M1);
`test_file_ops.py` (M2 — `file.*` executor + verifiers, unit + end-to-end);
`test_audit_query.py` (native audit-log reader);
`test_pdf_ops.py` (M3 — read-only `pdf.*` executor, unit + end-to-end);
`test_spreadsheet_ops.py` (M4 — `spreadsheet.*` executor + write verifier, unit + end-to-end);
`test_document_ops.py` (M6 — `document.*` executor + replace_text verifier, unit + end-to-end).
Current suite: **149 passed**.

## 7. Related docs

- `../docs/ARCHITECTURE.md` — the full architecture description (all required topics; repo-root shared docs).
- `../docs/ACTION_REFERENCE.md` — LLM-facing action/response reference (repo-root shared docs).
- `docs/QUIZ_NOTES.md` — anticipated review questions + answers.
- `CHANGES.md` — per-milestone/per-commit changelog.
