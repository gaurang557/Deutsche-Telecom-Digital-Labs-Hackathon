# Voice Agent — Frontend

Minimal Vite + React + TypeScript UI for the local voice-controlled agent.

## Prerequisites

- Node.js 18+ and npm
- The FastAPI backend running on `http://localhost:8000`
  (from the repo root: `./.venv/bin/uvicorn app.main:app --reload`)

## Setup

```bash
cd frontend
npm install
```

## Run

```bash
npm run dev
```

Open <http://localhost:5173>. The dev server proxies `/api` to the backend on
port 8000, so no CORS configuration is needed.

On load the app calls `GET /api/v1/voice/health` and shows the backend/model
status. Microphone capture and transcription land in later chunks.
