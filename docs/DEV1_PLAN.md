# Dev 1 — Voice / UI Plan (Speech-to-Text + Frontend)

Owner: Dev 1 (Voice/UI). Scope: capture the user's speech, turn it into a
`TaskRequest`, detect control intents, and provide an elegant minimal web UI.
Everything runs **locally** on macOS or Windows.

Shared contract lives in [`models-reference.md`](models-reference.md). Do not
fork the shared types.

---

## 1. Key decisions

### A. Speech-to-text engine: `faster-whisper`

| Option | Truly local? | Verdict |
|---|---|---|
| Browser Web Speech API (`webkitSpeechRecognition`) | No — Chrome streams audio to Google | Rejected (not local, Chrome-only) |
| **`faster-whisper` (CTranslate2)** | Yes — runs on local CPU | **Chosen** — cross-platform, per-segment confidence, matches team stack |
| whisper.cpp / Vosk | Yes | Fallback only (native build / lower accuracy) |

- **Model:** `small.en` preferred, `base.en` fallback (English-only models are
  more accurate for short commands). Choose based on the weakest demo laptop.
- **Compute type:** `int8` on CPU only. CTranslate2 has no Apple-MPS backend, so
  Mac and Windows both run CPU-int8 — identical behavior, no GPU assumptions.
- **Confidence:** derive `TaskRequest.confidence` from whisper's `avg_logprob`
  (e.g. `exp(avg_logprob)`) and inspect `no_speech_prob`. Low confidence must
  trigger a clarification, not planning.

### B. STT lives inside the shared FastAPI app (`app/`)

Flow: **browser records audio → POST to FastAPI → faster-whisper transcribes
locally → returns `TaskRequest` JSON.**

The mic lives in React; the model lives in Python. Put the endpoint in the
team's existing `app/` process (one `python -m agent` app, one contract) — not a
separate server.

- **Audio decode gotcha:** `MediaRecorder` emits WebM/Opus (Chrome) or OGG.
  faster-whisper decodes via PyAV, so add the `av` package and avoid needing a
  separate system `ffmpeg` binary — one less cross-platform install issue.

### C. TTS: browser `speechSynthesis` first, `pyttsx3` backend fallback

`window.speechSynthesis` uses on-device OS voices (local, cross-platform, zero
deps). Keep `pyttsx3` on the backend as a documented fallback.

---

## 2. Contract (what Dev 1 emits / accepts — do not fork)

Output to Dev 2:
```python
TaskRequest(request_id, text, source: "speech"|"text"|"test",
            confidence: float | None, received_at: datetime)
```

- **`request_id` = one `uuid4`, generated once at mic capture.** Never
  regenerate it — not on retry, not on re-record, not on the typed fallback.
  Every downstream `Action`, `AuditEvent`, and Dev 2's correction/revision
  scheme (`req1-s2b`) is keyed off this id. Regenerating it silently breaks the
  audit trail and replan logic.
- `source="speech"` for mic, `"text"` for typed fallback, `"test"` for fixtures.
- Control intents (`pause`, `resume`, `cancel`, `correct`) are detected **before**
  anything reaches the planner, via a deterministic matcher (not the LLM).

---

## 3. Backend plan (inside `app/`)

New module `app/voice/` + a router registered in `app/api/routes.py`.

| File | Responsibility |
|---|---|
| `app/voice/stt.py` | `transcribe_audio(bytes) -> Transcript`; singleton `WhisperModel` loaded once at startup |
| `app/voice/intents.py` | `detect_control_intent(text) -> ControlIntent \| None` (keyword/regex table) |
| `app/voice/schemas.py` | `Transcript`, `ControlIntent`, request/response models reusing shared `TaskRequest` |
| `app/voice/tts.py` | `speak()` pyttsx3 fallback (optional) |
| `app/api/voice_routes.py` | endpoints below |

Endpoints (`/api/v1`):
- `POST /transcribe` — multipart audio → `TaskRequest` (text, confidence, request_id, source, received_at)
- `POST /intent` — `{text}` → `ControlIntent | null`
- `POST /speak` — `{text}` → audio (only if backend-TTS fallback is used)
- `GET /voice/health` — reports whether the model is loaded (UI "warming up" state)

Config additions to `app/config.py`: `whisper_model` (`base.en`),
`whisper_compute_type` (`int8`), `whisper_model_dir` (local cache path),
`cors_origins`.

Startup: load the model on FastAPI startup; expose a `model_loaded` flag.

Deps to add: `faster-whisper`, `av`, `python-multipart`, `numpy`, optional `pyttsx3`.

---

## 4. Frontend plan (greenfield — Vite + React + TypeScript)

Dev on `:5173`, FastAPI on `:8000`, Vite dev proxy (`/api → :8000`) to avoid
CORS. For the packaged demo, FastAPI serves the Vite `dist/` build as static —
single command, single port.

Component tree:
```
App
├── MicButton        push-to-talk (hold spacebar / press-and-hold)
├── LevelMeter       live mic amplitude while recording
├── StatusPill       idle · listening · transcribing · error · model-warming
├── TranscriptCard   final text + confidence indicator
├── TextFallback     typed input → source="text" (mic-free testing / demo safety net)
└── ConfirmDialog    request_confirmation (yes/no, one-use token) [later, with Dev 4]
```

Recording flow: `getUserMedia` → `MediaRecorder` (start on hold, stop on
release) → `Blob` → `POST /transcribe` → render transcript + confidence. Use Web
Audio `AnalyserNode` only for the level meter, not for recording.

State machine: `idle → requesting-mic → listening → transcribing → result | error`.
Low-confidence result routes to a "did you mean…?" clarification, not the planner.

Design: one centered mic button as focal point, generous whitespace, single
accent color, calm transcript card below, subtle mic-pulse while listening,
system light/dark. One screen, no chrome.

---

## 5. Cross-platform + fully-local checklist

- `int8` CPU only — identical Mac/Windows behavior, no CUDA/MPS assumptions.
- `av` instead of system `ffmpeg` — avoids platform-specific audio-decode breakage.
- Model download is the one online step: faster-whisper fetches from HuggingFace
  on first run, then runs offline. For a guaranteed-offline demo, pre-download
  the model into a repo-local `models/` dir and point `whisper_model_dir` at it;
  script this so all laptops are identical.
- Mic needs a secure context — `localhost` counts, so plain `http://localhost`
  works with no HTTPS.

---

## 6. Testing (ASR error fixtures — deliverable)

- Fixture WAVs: clear command, noisy/low-confidence, silence, ambiguous phrase.
  Assert transcript + that low confidence triggers clarification.
- Control-intent unit tests: "cancel that", "stop", "pause", "actually change it
  to…" map to the correct `ControlIntent`.
- Boundary check: a transcript is always `source="speech"`; nothing read from a
  document ever becomes a `TaskRequest`.

---

## 7. Integration seams

- **→ Dev 2 (planner):** hand off a valid `TaskRequest`. Since it's one FastAPI
  process, prefer a direct in-process call over HTTP. Confirm in first 30 min.
- **Corrections:** when `detect_control_intent` returns `correct`, hand Dev 2 the
  correction text against the **same `request_id`** (their `-b` revision suffix
  scheme preserves completed history). A plain `cancel`/`pause` never reaches
  `create_plan`. Only `correct` triggers their replan path. Confirm the exact
  handoff payload with Dev 2.
- **← Dev 4 (safety):** `request_confirmation(summary, token)` and
  `ask_clarification(question)` — Dev 1 renders these UIs; token/policy come from
  Dev 4. Build against a mock token first.
- **← Dev 2/state:** UI reflects `TaskStatus` (running/paused/awaiting-confirmation).
  Stub now, wire later.

---

## 8. Build order (maps to the 5-hour Dev 1 column)

1. **0:00–0:30** — confirm `TaskRequest` fields, scaffold `app/voice/` + Vite app, `GET /voice/health` green.
2. **0:30–1:45** — STT vertical slice: `MediaRecorder` → `POST /transcribe` → text on screen.
3. **1:45–2:45** — push-to-talk polish, `detect_control_intent`, low-confidence → clarify.
4. **2:45–3:30** — text fallback, TTS, confirm/clarify UI.
5. **3:30–4:15** — fixtures + tests.
6. **4:15–5:00** — conversation polish, setup notes.

---

## 9. Open items to confirm with the team

1. Handoff mechanism to Dev 2: in-process call (recommended) vs. HTTP.
2. Model size: `base.en` (safe anywhere) vs `small.en` (better accuracy).
3. Correction handoff payload shape (same `request_id` + correction text).
