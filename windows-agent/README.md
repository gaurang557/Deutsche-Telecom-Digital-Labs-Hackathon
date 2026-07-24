# windows_agent — Voice-Controlled Computer Use Agent

A local AI agent that accepts a natural-language task, converts it into
**structured semantic actions**, **deterministically authorizes** those
actions, executes them against local files/applications, **independently
verifies** their effects, and returns structured results.

Windows is the primary target. The final runtime uses only open-source /
open-weight models and components. Common executors remain platform-neutral and
Windows UI automation stays behind an adapter, but macOS/Linux implementation
and testing are not scheduled.

> **One-line safety principle:** the LLM may *propose* actions, but only
> deterministic code may *authorize* them. This is enforced structurally, not
> by convention.

> **This is a self-contained module.** Everything for it lives under the
> `windows-agent/` folder inside the shared source tree; it has its own
> dependencies, tests, docs, and `.gitignore`, and does not depend on anything
> outside this folder. Run all commands below from inside `windows-agent/`.

---

## Project status (milestone-by-milestone)

| Milestone | Scope | Status |
|-----------|-------|--------|
| **M0** | Execution contract: shared schemas, `BaseExecutor`, `ActionRegistry`, `Dispatcher`, mock executors, tests | ✅ done |
| M1 | Policy/verification/audit interfaces and the safe dispatcher pipeline | ✅ done (mock policy/in-memory audit) |
| M2 | `file.*` executor, independent verifiers, native audit-log reader | ✅ done |
| M3 | Read-only `pdf.*` executor | ✅ done |
| M4 | `spreadsheet.*` executor + `write_cell` verifier | ✅ done |
| M5 | Deterministic multi-app orchestration workflow | deferred to planner integration / M14 |
| M6 | `document.*` executor + `replace_text` verifier | ✅ done |
| M7 | Presentation executor | planned |
| M8 | Document→presentation orchestration | deferred to planner/LLM integration / M14; not hardcoded |
| M9 | Windows UI Automation adapter | planned |
| M10 | Browser executor | planned |
| M11 | Shared audit implementation | external/team-owned; this module supplies action events and integration/E2E tests |
| M12 | Deterministic policy, risk, and confirmation | mandatory work in this execution/safety module |
| M13 | Pause/resume/cancel/correction integration | planned |
| M14 | Planner/LLM integration, including deferred M5/M8 orchestration | planned |
| M15 | End-to-end regression | planned |
| M16 | Hardening | planned |
| M17 | Cross-platform/macOS readiness | removed from active roadmap |
| M18 | Release/demo freeze | planned |

M0–M4 and M6 code exist today. Desktop, browser, planner/LLM, voice, full state
orchestration, and shared-audit integration remain planned or externally owned
as shown above.

---

## Requirements

- Python 3.11+
- Dependencies currently cover the core plus implemented structured-file
  executors: `pydantic`, `pytest`, `pytest-asyncio`, PyMuPDF, openpyxl, and
  python-docx (see `requirements.txt` for version floors).

Heavier dependencies (PyMuPDF, openpyxl, python-docx, python-pptx, Playwright,
pywinauto, faster-whisper, etc.) are added by the milestone that needs them, so
early setup stays fast and portable.

## Setup

```powershell
# from inside the windows-agent/ module folder
cd windows-agent
python -m venv .venv
.venv\Scripts\activate          # PowerShell on Windows
pip install -r requirements.txt
```

## Run the tests

```powershell
# from inside windows-agent/
pytest -q
```

`pytest.ini` sets `asyncio_mode=auto` (so `async def test_*` runs without
decorators) and `pythonpath=.` (so `import windows_agent` resolves from the
repo root).

---

## Repository structure

Everything below is rooted at the `windows-agent/` module folder:

```
windows_agent/
  contracts/            # shared, JSON-serialisable Pydantic schemas (the integration surface)
    enums.py            #   ActionStatus, VerificationStatus, ErrorCode
    action.py           #   Action  — what the planner proposes (no authority fields allowed)
    error.py            #   ActionError — structured failure
    verification.py     #   VerificationResult — independent check outcome
    results.py          #   ExecutorResult (internal) + ActionResult (to planner)
  executors/
    base.py             # BaseExecutor — async execution contract
    common/
      mock.py           #   EchoExecutor / FailingExecutor / RaisingExecutor (test doubles)
  execution/
    registry.py         # ActionRegistry — action type -> executor
    dispatcher.py       # Dispatcher — the single, safe execution path
tests/
  test_contracts.py     # schema validation (incl. forbidden-field rejection)
  test_dispatcher.py    # dispatch success/failure + unknown-action safety + evidence bounding
docs/
  WALKTHROUGH.md        # guided tour: how the files fit together + reading order
  QUIZ_NOTES.md         # study notes / anticipated Q&A
CHANGES.md              # per-milestone / per-commit changelog
requirements.txt
pytest.ini
```

> **Documentation convention:** detailed "what it does and why" explanations
> live in each source file's module docstring and inline comments. This README
> and `../docs/ARCHITECTURE.md` are intentionally high-level intros — open the file
> itself for the specifics. The team-shared deliverables — `../docs/ARCHITECTURE.md`
> and the canonical planner vocabulary `../docs/ACTION_REFERENCE.md` — live in
> the repo-root `docs/`.

## How the execution path works (today)

```
Action (Pydantic-validated)
   └─> Dispatcher.dispatch(action)          # single entry point (async)
         ├─ registry.get(action.type)       # unknown type -> safe FAILED result
         ├─ await executor.execute(action)  # returns ExecutorResult (internal)
         ├─ bound evidence                  # cap sizes; never leak whole files
         └─> ActionResult (to planner)      # status + bounded evidence + error
```

Reserved (added in later milestones, in this exact spot):
`validate → [policy authorize] → [confirmation] → execute → [verification] → [audit]`.

See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the full design and the
reasoning behind each decision.
