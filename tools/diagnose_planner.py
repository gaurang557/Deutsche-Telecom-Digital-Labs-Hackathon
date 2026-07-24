"""THROWAWAY DIAGNOSTIC — delete before the freeze.

Runs the failing Demo 1 sentence through the real planner path (few-shot + repair
loop) N times against live Ollama and reports schema validity plus semantic
equivalence to the deterministic golden plan.

    & ".\.venv\Scripts\python.exe" tools\diagnose_planner.py 5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.planning.exceptions import InvalidPlannerResponseError
from app.planning.planner import SYSTEM_PROMPT, OllamaPlanner
from app.schemas import DraftPlan, TaskRequest

SENTENCE = (
    "in desktop fixtures Find the North Region revenue in quarterly_report.pdf "
    "and put it in the North row of results_blank.xlsx"
)


def semantic_check(draft: DraftPlan) -> tuple[bool, list[str]]:
    """Is this plan semantically the golden pdf->xlsx plan?"""
    problems: list[str] = []
    types = [str(action.type) for action in draft.actions]

    pdf_steps = [a for a in draft.actions if str(a.type).startswith("pdf.")]
    write_steps = [
        a for a in draft.actions if str(a.type) == "spreadsheet.write_cell"
    ]

    if not pdf_steps:
        problems.append(f"no pdf.* read step (types={types})")
    else:
        for step in pdf_steps:
            if not step.target.casefold().endswith(".pdf"):
                problems.append(f"pdf step targets {step.target!r}")
            if "quarterly_report" not in step.target.casefold():
                problems.append(f"wrong pdf source {step.target!r}")

    if len(write_steps) != 1:
        problems.append(f"expected exactly 1 spreadsheet.write_cell, got {len(write_steps)}")
    else:
        write = write_steps[0]
        if not write.target.casefold().endswith(".xlsx"):
            problems.append(f"write targets {write.target!r}")
        if "results_blank" not in write.target.casefold():
            problems.append(f"wrong workbook {write.target!r}")
        value = write.parameters.get("value")
        if not isinstance(value, dict) or "$ref" not in value:
            problems.append(f"value is not a $ref (got {value!r}) -> model invented it")
        else:
            ref = value.get("$ref", "")
            source_keys = {a.step_key for a in pdf_steps}
            if not any(ref.startswith(f"{key}.") for key in source_keys):
                problems.append(f"$ref {ref!r} does not point at a pdf step")
            if not value.get("regex"):
                problems.append("$ref has no regex to extract the number")
        cell = write.parameters.get("cell")
        if not isinstance(cell, str) or not cell:
            problems.append(f"no target cell ({cell!r})")
        elif cell.upper() != "B2":
            problems.append(
                f"wrong cell {cell!r}: North is row 2 in the fixture, expected B2"
            )
        if write.parameters.get("sheet") != "Summary":
            problems.append(f"sheet is {write.parameters.get('sheet')!r}, expected 'Summary'")

    return not problems, problems


def main() -> None:
    settings = Settings()
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"model={settings.ollama_model}  runs={runs}  prompt={len(SYSTEM_PROMPT)} chars")
    print("=" * 78)

    planner = OllamaPlanner(settings)
    valid = 0
    golden = 0
    for run in range(1, runs + 1):
        print(f"\n########## RUN {run} ##########")
        try:
            draft = planner._create_draft_sync(TaskRequest(text=SENTENCE))
        except InvalidPlannerResponseError as exc:
            print(f"FAILED after all repair attempts: {exc}")
            continue
        valid += 1
        ok, problems = semantic_check(draft)
        golden += ok
        print(f"summary: {draft.summary}")
        for action in draft.actions:
            print(
                f"  - {action.step_key}: {action.type} target={action.target!r}\n"
                f"      params={json.dumps(action.parameters)}"
            )
        print(f"SCHEMA VALID: yes    SEMANTICALLY GOLDEN: {'yes' if ok else 'NO'}")
        for problem in problems:
            print(f"      ! {problem}")

    print("\n" + "=" * 78)
    print(f"schema-valid : {valid}/{runs}")
    print(f"golden-equiv : {golden}/{runs}")


if __name__ == "__main__":
    main()
