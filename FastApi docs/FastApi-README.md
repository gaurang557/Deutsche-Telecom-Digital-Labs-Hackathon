# Voice desk

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

After planning, the UI asks the user to approve execution. Actions marked as
requiring confirmation are listed explicitly in the prompt. The approved plan
is submitted once to:

```text
POST /api/v1/plans/{plan_id}/execute
```

The MVP executor supports native application launch on macOS and Windows,
PyAutoGUI keyboard/mouse input, local text-file operations, and recoverable file
deletion through the operating system trash. Sending messages, submitting
forms, and publishing remain blocked until dedicated application adapters are
implemented.

## Test

```bash
pytest
ruff check .
```
