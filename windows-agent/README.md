# windows_agent — Voice-Controlled Computer Use Agent

A local AI agent that accepts a natural-language task, converts it into
**structured semantic actions**, **deterministically authorizes** those
actions, executes them against local files/applications, **independently
verifies** their effects, and returns structured results.

Windows is the primary target. The final runtime uses only open-source /
open-weight models and components. The architecture is deliberately split so a
macOS adapter can be added later without touching the planner, policy, state,
audit, or action contracts.

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
| M1 | Deterministic **Policy Engine** (risk classes, `PolicyDecision`, `action_hash`-bound `Confirmation`) | ⏳ next |
| M2 | Real executors (file/PDF/spreadsheet) + **Verification** | planned |
| M3 | State machine (pause/resume/cancel/correction) + **Audit** (SQLite, redaction) | planned |
| M4 | Windows desktop adapter (UI Automation) + browser (Playwright) | planned |
| M5 | Planner (local LLM) + Voice pipeline (ASR/TTS) | planned |

Only M0 code exists today. The architecture document describes the full
intended design and marks what is implemented vs planned.

---

## Requirements

- Python 3.11+
- Dependencies (M0 only, intentionally minimal):
  - `pydantic>=2.6`, `pytest>=8.0`, `pytest-asyncio>=0.23`

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
> and `../docs/ACTION_REFERENCE.md` — live in the repo-root `docs/`.

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
