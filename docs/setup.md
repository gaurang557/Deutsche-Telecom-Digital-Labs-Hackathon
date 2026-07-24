# Local Setup — macOS and Windows

This guide runs the complete local application:

```text
Browser microphone → Faster-Whisper → FastAPI → Ollama planner
                                      → approval → desktop executor
```

## Prerequisites

Install the following before continuing:

- Python 3.11 or newer
- Node.js 20 or newer and npm
- Ollama
- Git
- A microphone

Run every command from the repository root unless the guide says otherwise.

## 1. Install and prepare Ollama

Start Ollama using the desktop application or:

```bash
ollama serve
```

Keep that process running. In another terminal, download the configured model:

```bash
ollama pull llama3.2
ollama list
```

`ollama list` must show `llama3.2`. A quick model check is:

```bash
ollama run llama3.2 "Return only the word ready"
```

Enter `/bye` to leave the interactive session.

## 2. Configure the application

### macOS

```bash
cp .env.example .env
```

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

The default configuration connects to Ollama at
`http://localhost:11434` and uses `llama3.2`. To use another installed
model, edit `.env`:

```env
AGENT_OLLAMA_MODEL=your-model-name
```

The default Whisper model is `base.en`. Its weights are downloaded on the
first transcription. To load Whisper while FastAPI starts instead, set:

```env
AGENT_WARM_WHISPER_ON_STARTUP=true
```

## 3. Install the backend

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The execution-policy change applies only to the current PowerShell process.

## 4. Install the frontend

### macOS

```bash
cd frontend
npm install
cd ..
```

### Windows PowerShell

```powershell
Set-Location frontend
npm install
Set-Location ..
```

## 5. Grant operating-system permissions

### macOS

macOS protects microphone input, desktop control, Desktop, Documents, and
Downloads. Grant permissions to the application that launches the backend,
such as Terminal, iTerm, Visual Studio Code, or Codex.

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Enable the terminal or editor that will run Uvicorn.
3. Open **Privacy & Security → Files and Folders**.
4. Enable **Desktop Folder**, **Documents Folder**, and **Downloads Folder**
   for that application.
5. If the application is not listed, add it under **Full Disk Access**.
6. Quit and reopen the terminal or editor after changing permissions.

The browser also asks for microphone access when push-to-talk is first used.
Select **Allow**.

Verify Downloads access from the same terminal that will run Uvicorn:

```bash
ls "$HOME/Downloads"
```

If this prints `Operation not permitted`, the permission was granted to a
different application or the application has not been restarted.

### Windows

1. Open **Settings → Privacy & security → Microphone**.
2. Enable **Microphone access** and **Let desktop apps access your
   microphone**.
3. Allow microphone access when the browser requests it.

PyAutoGUI controls the interactive desktop session. Keep the target
applications visible and unlocked. Run the backend as the same Windows user
that owns the desktop session. Avoid running it as Administrator unless a
specific target application is also elevated and requires it.

## 6. Start the backend

Open a terminal in the repository root.

### macOS

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

### Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Verify:

- API root: <http://localhost:8000>
- API documentation: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/api/v1/health>
- Voice health: <http://localhost:8000/api/v1/voice/health>

## 7. Start the frontend

Keep the backend and Ollama running. Open another terminal:

### macOS

```bash
cd frontend
npm run dev
```

### Windows PowerShell

```powershell
Set-Location frontend
npm run dev
```

Open <http://localhost:5173>. The Vite development server proxies `/api`
requests to FastAPI on port `8000`.

## 8. Use the application

1. Hold the microphone button or spacebar.
2. Speak a request.
3. Release the button or spacebar.
4. Review the transcript and generated action plan.
5. Select **Approve and execute**.
6. Confirm any consequential actions shown in the confirmation dialog.
7. Review the result reported for every attempted action.

Example:

```text
Open the latest PDF in the Downloads folder.
```

The planner should generate one `open_file` action. The executor selects the
newest PDF and opens it using the operating system's default PDF application.

## Choosing the planner backend

The planner generates plans through one of two backends, selected by
`AGENT_LLM_PROVIDER`:

| Value | Backend | Notes |
| --- | --- | --- |
| `ollama` | Local Ollama (default) | Uses JSON-schema constrained decoding. |
| `cursor` | Cursor SDK | Requires the optional SDK and an API key. |

Leaving `AGENT_LLM_PROVIDER` unset keeps the local Ollama behaviour described
above. To use Cursor instead, install the optional dependency and set the
credential in the shell environment. It is read from `CURSOR_API_KEY` by the
SDK and is deliberately not an application setting, so never put it in `.env`.

### macOS

```bash
pip install -e ".[cursor]"
export CURSOR_API_KEY="cursor_..."
export AGENT_LLM_PROVIDER=cursor
```

### Windows PowerShell

```powershell
pip install -e ".[cursor]"
$env:CURSOR_API_KEY = "cursor_..."
$env:AGENT_LLM_PROVIDER = "cursor"
```

`AGENT_CURSOR_MODEL` selects the model and defaults to `composer-2.5`. Restart
FastAPI after changing either variable. Cursor requests consume Cursor credits,
and there is no automatic fallback to Ollama: a Cursor failure is reported as a
failure. Cursor cannot constrain decoding to the plan schema, so the schema is
requested in the prompt and the planner's own validation and repair loop
remains what decides whether a plan is acceptable.

## Tests and build verification

With the Python environment activated:

```bash
pytest
ruff check app tests
npm --prefix frontend run build
```

## Troubleshooting

### Ollama is unavailable

Symptoms include HTTP `503` or `Ollama is unavailable`.

```bash
ollama list
```

Confirm Ollama is running, `llama3.2` is installed, and `.env` contains:

```env
AGENT_OLLAMA_BASE_URL=http://localhost:11434
AGENT_OLLAMA_MODEL=llama3.2
```

Restart FastAPI after editing `.env`.

### Whisper is slow on the first request

The first transcription may download and initialize `base.en`. Keep the
backend connected to the internet until the download completes. Later
transcriptions use the local cache.

### macOS reports `Operation not permitted`

Grant Files and Folders access to the exact terminal or editor process running
Uvicorn, quit it completely, reopen it, and restart FastAPI. Previously
submitted plans are single-use; record the request again after restarting.

### Gmail summarization on macOS

Voice desk can summarize the email currently open in Gmail in Google Chrome.
The feature reads the visible Gmail page and sends that text only to the local
Ollama model.

1. Open Gmail in Google Chrome and select the email to summarize.
2. In Chrome, enable **View → Developer → Allow JavaScript from Apple
   Events**.
3. Grant Automation permission if macOS asks whether the terminal may control
   Google Chrome.
4. Ask: `Summarize the email currently open in Gmail.`

The email is treated as untrusted content. Instructions written inside an
email cannot authorize desktop actions.

### Closing all applications on macOS

`Close all apps` requires plan approval because applications may contain
unsaved work. Voice desk preserves Finder and the terminal or editor hosting
the backend so it can finish the execution request. Other visible applications
are asked to quit and may still display their normal save confirmation.

### Desktop input does not occur

- On macOS, confirm Accessibility permission for the Uvicorn host application.
- On Windows, keep the desktop unlocked and the target window visible.
- Do not move the mouse to a screen corner while PyAutoGUI is working; its
  fail-safe treats that as an emergency stop.
- Generate a new plan after an execution attempt. Plans cannot be submitted
  twice.

### Frontend cannot reach the backend

Confirm FastAPI is listening on port `8000` and Vite on port `5173`. Open the
health endpoint directly before retrying the frontend.
