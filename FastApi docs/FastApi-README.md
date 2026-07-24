# Voice-Controlled Desktop Agent

FastAPI backend for a local voice-controlled agent that performs desktop
operations.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

## Run

```bash
uvicorn app.main:app --reload
```

Open:

- API documentation: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/api/v1/health>
- Plan a transcript: `POST http://127.0.0.1:8000/api/v1/plans`

## Planning API

The planning endpoint accepts speech-to-text output:

```json
{
  "text": "Open Calculator and calculate 25 multiplied by 4",
  "source": "speech"
}
```

Ollama must be running locally with the model configured by
`AGENT_OLLAMA_MODEL`. The response contains application-generated plan and
action UUIDs, ordered dependencies, deterministic risk classification, and
confirmation requirements. Control commands such as `pause`, `resume`, and
`cancel` are detected before calling Ollama.

Planning does not execute desktop actions. Pass the validated `plan` object to
the team's execution engine, which should return an `ActionResult` for each
action and verify `expected_result` before advancing.

## Test

```bash
pytest
ruff check .
```
