# Public hackathon demo

The `codex/hackathon-cloud-demo` branch supports a public, sandboxed deployment.
It must never be presented as controlling the visitor's real computer.

## Runtime modes

- Local mode is the default. It uses Ollama and `DesktopExecutor`.
- Demo mode is enabled with `AGENT_DEMO_MODE=true`. It uses the hosted-planner
  adapter with a resilient demo fallback and `DemoDesktopExecutor`.

The demo executor never invokes desktop commands. It returns realistic evidence
from sample files under `AGENT_DEMO_SANDBOX_DIR`.

## Cloud environment

```env
AGENT_DEMO_MODE=true
AGENT_PLANNER_PROVIDER=bedrock
AGENT_BEDROCK_REGION=us-east-1
AGENT_BEDROCK_MODEL_ID=amazon.nova-micro-v1:0
AGENT_DB=/data/agent_store.db
```

Amazon Bedrock requires model authorization and workload credentials. If it is
temporarily unavailable, the public showcase falls back to a deterministic
planner so planning, approval, controls, history, and verification remain
demonstrable.

## Container

The production image builds the React frontend and serves it from FastAPI. It
installs only cloud dependencies; native desktop automation and local Whisper
packages are deliberately excluded.

Browser speech recognition is used in demo mode. Chrome or Edge is recommended.
Typing remains available when browser speech recognition is unsupported.

## Safety boundary

Never change the public deployment to `AGENT_DEMO_MODE=false`. A public API must
not expose the real desktop executor or private user filesystem.
