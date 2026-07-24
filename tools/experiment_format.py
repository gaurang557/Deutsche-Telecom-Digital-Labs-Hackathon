"""THROWAWAY EXPERIMENT — delete before the freeze.

Isolates whether Ollama's `format` (constrained decoding against the DraftPlan
JSON schema) is what truncates the actions array to one entry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.planning.planner import SYSTEM_PROMPT, WORKED_EXAMPLE
from app.schemas import DraftPlan

SENTENCE = (
    "in desktop fixtures Find the North Region revenue in quarterly_report.pdf "
    "and put it in the North row of results_blank.xlsx"
)


def chat(settings: Settings, *, use_format: bool, nudge: bool) -> str:
    system = SYSTEM_PROMPT + WORKED_EXAMPLE
    if not use_format:
        system += (
            "\nReturn ONLY a JSON object with keys summary and actions. "
            "No markdown fences, no commentary.\n"
        )
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": SENTENCE},
        ],
        "stream": False,
        "options": {"temperature": 0},
    }
    if use_format:
        schema = DraftPlan.model_json_schema()
        if nudge:
            schema["properties"]["actions"]["minItems"] = 3
        payload["format"] = schema

    request = Request(
        f"{settings.ollama_base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
        return json.load(response)["message"]["content"]


def report(label: str, content: str) -> None:
    print(f"\n===== {label} =====")
    print(f"raw ({len(content)} chars):")
    print(content[:2500])
    try:
        draft = DraftPlan.model_validate_json(content)
    except Exception as exc:  # noqa: BLE001 - experiment only
        print(f"-> INVALID: {type(exc).__name__}: {str(exc)[:600]}")
        return
    print(f"-> VALID with {len(draft.actions)} actions: "
          f"{[str(a.type) for a in draft.actions]}")


def main() -> None:
    settings = Settings()
    report("A: format=on (current behaviour)", chat(settings, use_format=True, nudge=False))
    report("B: format=off, prose instruction", chat(settings, use_format=False, nudge=False))
    report("C: format=on, minItems=3", chat(settings, use_format=True, nudge=True))


if __name__ == "__main__":
    main()
