"""THROWAWAY EXPERIMENT — delete before the freeze."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.planning.planner import (
    SYSTEM_PROMPT,
    WORKED_EXAMPLE,
    OllamaPlanner,
    extract_json_object,
)
from app.schemas import DraftPlan

SENTENCE = (
    "in desktop fixtures Find the North Region revenue in quarterly_report.pdf "
    "and put it in the North row of results_blank.xlsx"
)


def main() -> None:
    planner = OllamaPlanner(Settings())
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + WORKED_EXAMPLE},
        {"role": "user", "content": SENTENCE},
    ]
    content = planner._chat(messages)
    print(f"--- RAW ({len(content)} chars) ---")
    print(content)
    print("--- EXTRACTED ---")
    extracted = extract_json_object(content)
    print(extracted)
    print("--- VALIDATION ---")
    try:
        draft = DraftPlan.model_validate_json(extracted)
    except Exception as exc:  # noqa: BLE001 - experiment only
        print(f"{type(exc).__name__}: {exc}")
        return
    print(f"VALID: {[str(a.type) for a in draft.actions]}")


if __name__ == "__main__":
    main()
