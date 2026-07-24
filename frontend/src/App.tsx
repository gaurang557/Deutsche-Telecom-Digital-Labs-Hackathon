import { useEffect, useState } from "react";
import {
  createPlan,
  executePlan,
  type PlanExecutionResponse,
  type PlanningResponse,
} from "./api";
import { useVoiceCapture } from "./useVoiceCapture";

interface VoiceHealth {
  status: string;
  model: string;
  model_loaded: boolean;
}

const LOW_CONFIDENCE = 0.5;

const STATUS_LABEL: Record<string, string> = {
  idle: "Hold the mic and speak",
  requesting: "Allow microphone access…",
  recording: "Listening…",
  transcribing: "Transcribing…",
  error: "Something went wrong",
};

export default function App() {
  const [health, setHealth] = useState<VoiceHealth | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planningResult, setPlanningResult] =
    useState<PlanningResponse | null>(null);
  const [planningError, setPlanningError] = useState<string | null>(null);
  const [executing, setExecuting] = useState(false);
  const [executionResult, setExecutionResult] =
    useState<PlanExecutionResponse | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const { status, result, error, start, stop } = useVoiceCapture();

  const recording = status === "recording";
  const busy = status === "requesting" || status === "transcribing";

  useEffect(() => {
    fetch("/api/v1/voice/health")
      .then((res) => (res.ok ? res.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    if (!result?.text) return;

    let active = true;
    setPlanning(true);
    setPlanningError(null);
    setPlanningResult(null);
    setExecutionResult(null);
    setExecutionError(null);

    createPlan(result)
      .then((response) => {
        if (active) setPlanningResult(response);
      })
      .catch((reason: unknown) => {
        if (active) {
          setPlanningError(
            reason instanceof Error ? reason.message : "Planning failed",
          );
        }
      })
      .finally(() => {
        if (active) setPlanning(false);
      });

    return () => {
      active = false;
    };
  }, [result]);

  // Spacebar as an alternative push-to-talk trigger.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat) {
        e.preventDefault();
        start();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        stop();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [start, stop]);

  const lowConfidence =
    result?.confidence != null && result.confidence < LOW_CONFIDENCE;

  const approveAndExecute = async () => {
    const plan = planningResult?.plan;
    if (!plan) return;

    const riskyActions = plan.actions.filter(
      (action) => action.requires_confirmation,
    );
    const warning =
      riskyActions.length > 0
        ? `\n\nThe following actions require explicit confirmation:\n${riskyActions
            .map((action) => `• ${action.type}: ${action.target}`)
            .join("\n")}`
        : "";
    if (!window.confirm(`Execute this plan?\n\n${plan.summary}${warning}`)) {
      return;
    }

    setExecuting(true);
    setExecutionError(null);
    try {
      const response = await executePlan(
        plan.plan_id,
        riskyActions.map((action) => action.action_id),
      );
      setExecutionResult(response);
    } catch (reason: unknown) {
      setExecutionError(
        reason instanceof Error ? reason.message : "Execution failed",
      );
    } finally {
      setExecuting(false);
    }
  };

  return (
    <main className="app">
      <h1 className="title">Voice Agent</h1>
      <p className="subtitle">Local voice-controlled desktop agent</p>

      <button
        className={`mic ${recording ? "mic--recording" : ""}`}
        onPointerDown={(e) => {
          e.preventDefault();
          start();
        }}
        onPointerUp={stop}
        onPointerLeave={() => {
          if (recording) stop();
        }}
        disabled={busy}
        aria-label="Hold to talk"
      >
        <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
          <path
            fill="currentColor"
            d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z"
          />
          <path
            fill="currentColor"
            d="M19 12a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.92V21a1 1 0 1 0 2 0v-2.08A7 7 0 0 0 19 12Z"
          />
        </svg>
      </button>

      <p className={`status status--${status}`}>{STATUS_LABEL[status]}</p>

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="transcript">
          <p className="transcript__text">
            {result.text ? `“${result.text}”` : "(no speech detected)"}
          </p>
          <p className="transcript__meta">
            {result.confidence != null
              ? `confidence ${(result.confidence * 100).toFixed(0)}%`
              : "confidence —"}
            {lowConfidence && " · low confidence, please repeat"}
          </p>
        </div>
      )}

      {planning && <p className="status">Planning actions…</p>}
      {planningError && <p className="error">{planningError}</p>}
      {planningResult?.control_intent && (
        <div className="transcript">
          <p className="transcript__text">
            Control command: {planningResult.control_intent}
          </p>
        </div>
      )}
      {planningResult?.plan && (
        <div className="transcript">
          <p className="transcript__text">{planningResult.plan.summary}</p>
          <ol>
            {planningResult.plan.actions.map((action) => (
              <li key={action.action_id}>
                {action.type}: {action.target}
                {action.requires_confirmation && " · confirmation required"}
              </li>
            ))}
          </ol>
          {!executionResult && (
            <button
              type="button"
              onClick={approveAndExecute}
              disabled={executing}
            >
              {executing ? "Executing…" : "Approve and execute"}
            </button>
          )}
        </div>
      )}
      {executionError && <p className="error">{executionError}</p>}
      {executionResult && (
        <div className="transcript">
          <p className="transcript__text">
            Execution {executionResult.status}
          </p>
          <ol>
            {executionResult.results.map((action) => (
              <li key={action.action_id}>
                {action.status}
                {action.error ? ` · ${action.error}` : ""}
              </li>
            ))}
          </ol>
        </div>
      )}

      {health && (
        <footer className="health">
          backend {health.status} · model {health.model} ·{" "}
          {health.model_loaded ? "loaded" : "not loaded"}
        </footer>
      )}
    </main>
  );
}
