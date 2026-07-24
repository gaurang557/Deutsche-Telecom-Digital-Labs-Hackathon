import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  createPlan,
  executePlan,
  type PlanExecutionResponse,
  type PlanningResponse,
  type TaskRequest,
} from "./api";
import { useVoiceCapture } from "./useVoiceCapture";

interface VoiceHealth {
  status: string;
  model: string;
  model_loaded: boolean;
}

const LOW_CONFIDENCE = 0.5;

const CAPTURE_LABEL: Record<string, string> = {
  idle: "Hold to speak",
  requesting: "Waiting for microphone access…",
  recording: "Listening — release when you're done",
  transcribing: "Turning your voice into text…",
  error: "I couldn't access the microphone",
};

function friendlyExecutionMessage(result: PlanExecutionResponse): string {
  if (result.status === "completed") {
    return "Done — everything in the plan completed successfully.";
  }
  if (result.status === "blocked") {
    return "I paused because one of the actions needs your attention.";
  }
  if (result.status === "cancelled") {
    return "The task was cancelled. Nothing else will be changed.";
  }
  return "I couldn't finish the task. The details below should help us fix it.";
}

function readableStatus(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export default function App() {
  const [health, setHealth] = useState<VoiceHealth | null>(null);
  const [activeRequest, setActiveRequest] = useState<TaskRequest | null>(null);
  const [draft, setDraft] = useState("");
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
  const captureBusy = status === "requesting" || status === "transcribing";
  const appBusy = captureBusy || planning || executing;

  useEffect(() => {
    fetch("/api/v1/voice/health")
      .then((response) => (response.ok ? response.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const requestPlan = useCallback(async (request: TaskRequest) => {
    setActiveRequest(request);
    setPlanning(true);
    setPlanningError(null);
    setPlanningResult(null);
    setExecutionResult(null);
    setExecutionError(null);

    try {
      setPlanningResult(await createPlan(request));
    } catch (reason: unknown) {
      setPlanningError(
        reason instanceof Error
          ? reason.message
          : "I couldn't prepare that plan.",
      );
    } finally {
      setPlanning(false);
    }
  }, []);

  useEffect(() => {
    if (result?.text) void requestPlan(result);
  }, [requestPlan, result]);

  const submitText = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || appBusy) return;
    setDraft("");
    void requestPlan({
      request_id: crypto.randomUUID(),
      text,
      source: "text",
      confidence: null,
      received_at: new Date().toISOString(),
    });
  };

  const approveAndExecute = async () => {
    const plan = planningResult?.plan;
    if (!plan || executing) return;

    const approvedActionIds = plan.actions
      .filter((action) => action.requires_confirmation)
      .map((action) => action.action_id);

    setExecuting(true);
    setExecutionError(null);
    try {
      setExecutionResult(await executePlan(plan.plan_id, approvedActionIds));
    } catch (reason: unknown) {
      setExecutionError(
        reason instanceof Error
          ? reason.message
          : "I couldn't execute that plan.",
      );
    } finally {
      setExecuting(false);
    }
  };

  const resetConversation = () => {
    setActiveRequest(null);
    setPlanningResult(null);
    setPlanningError(null);
    setExecutionResult(null);
    setExecutionError(null);
    setDraft("");
  };

  const lowConfidence =
    activeRequest?.confidence != null &&
    activeRequest.confidence < LOW_CONFIDENCE;
  const hasConversation =
    activeRequest || planning || planningResult || planningError;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand__mark">A</span>
          <span>Aura Desktop</span>
        </div>

        <button className="new-task" type="button" onClick={resetConversation}>
          <span aria-hidden="true">＋</span>
          New task
        </button>

        <div className="sidebar__section">
          <p className="sidebar__label">Workspace</p>
          <div className="sidebar__item sidebar__item--active">
            <span className="sidebar__glyph" aria-hidden="true">⌁</span>
            Desktop assistant
          </div>
        </div>

        <div className="system-card">
          <div className="system-card__row">
            <span
              className={`status-dot ${health ? "status-dot--online" : ""}`}
              aria-hidden="true"
            />
            <span>{health ? "Systems ready" : "Connecting…"}</span>
          </div>
          <p>
            {health
              ? `${health.model} · ${health.model_loaded ? "Voice ready" : "Voice loads on first use"}`
              : "Checking local services"}
          </p>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">LOCAL AI AGENT</p>
            <h1>Desktop assistant</h1>
          </div>
          <div className="privacy-pill">
            <span aria-hidden="true">◆</span>
            Private &amp; on-device
          </div>
        </header>

        <section
          className={`conversation ${hasConversation ? "" : "conversation--empty"}`}
          aria-live="polite"
        >
          {!hasConversation && (
            <div className="welcome">
              <div className="welcome__orb" aria-hidden="true">
                <span />
                <span />
                <span />
              </div>
              <p className="eyebrow">READY WHEN YOU ARE</p>
              <h2>What can I take care of?</h2>
              <p>
                Ask me to open apps, work with files, or handle a desktop task.
                You’ll always review the plan before anything happens.
              </p>
              <div className="suggestions">
                <button
                  type="button"
                  onClick={() =>
                    setDraft("Open the latest PDF in my Downloads folder")
                  }
                >
                  Open my latest PDF
                </button>
                <button
                  type="button"
                  onClick={() => setDraft("Open Calculator")}
                >
                  Launch an application
                </button>
              </div>
            </div>
          )}

          {activeRequest && (
            <article className="message message--user">
              <div className="message__avatar">You</div>
              <div className="message__content">
                <p>{activeRequest.text}</p>
                {activeRequest.source === "speech" && (
                  <span className={lowConfidence ? "confidence--low" : ""}>
                    Voice transcript
                    {activeRequest.confidence != null &&
                      ` · ${Math.round(activeRequest.confidence * 100)}% confidence`}
                  </span>
                )}
              </div>
            </article>
          )}

          {planning && (
            <article className="message message--assistant">
              <div className="assistant-avatar">A</div>
              <div className="message__content">
                <div className="thinking">
                  <span />
                  <span />
                  <span />
                </div>
                <small>Thinking through the safest way to do that…</small>
              </div>
            </article>
          )}

          {planningError && (
            <article className="message message--assistant">
              <div className="assistant-avatar">A</div>
              <div className="message__content">
                <p>I ran into a problem while preparing your plan.</p>
                <div className="notice notice--error">{planningError}</div>
              </div>
            </article>
          )}

          {planningResult?.control_intent && (
            <article className="message message--assistant">
              <div className="assistant-avatar">A</div>
              <div className="message__content">
                <p>
                  Understood — I’ll {planningResult.control_intent} the current
                  task.
                </p>
              </div>
            </article>
          )}

          {planningResult?.plan && (
            <article className="message message--assistant">
              <div className="assistant-avatar">A</div>
              <div className="message__content">
                <p>{planningResult.plan.summary}</p>

                <div className="plan-card">
                  <div className="plan-card__header">
                    <div>
                      <span className="plan-card__kicker">PROPOSED PLAN</span>
                      <strong>
                        {planningResult.plan.actions.length}{" "}
                        {planningResult.plan.actions.length === 1
                          ? "step"
                          : "steps"}
                      </strong>
                    </div>
                    <span className="review-badge">Ready to review</span>
                  </div>

                  <ol className="plan-steps">
                    {planningResult.plan.actions.map((action, index) => (
                      <li key={action.action_id}>
                        <span className="step-number">{index + 1}</span>
                        <div>
                          <p>{action.description}</p>
                          {action.requires_confirmation && (
                            <span className="risk-note">
                              This step changes something outside the app
                            </span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>

                  {!executionResult && (
                    <div className="plan-card__footer">
                      <p>
                        Nothing runs until you approve this plan.
                      </p>
                      <button
                        className="primary-action"
                        type="button"
                        onClick={approveAndExecute}
                        disabled={executing}
                      >
                        {executing ? (
                          <>
                            <span className="button-spinner" />
                            Working on it…
                          </>
                        ) : (
                          <>
                            Approve &amp; execute
                            <span aria-hidden="true">→</span>
                          </>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </article>
          )}

          {executionError && (
            <article className="message message--assistant">
              <div className="assistant-avatar">A</div>
              <div className="message__content">
                <p>I couldn’t start the task.</p>
                <div className="notice notice--error">{executionError}</div>
              </div>
            </article>
          )}

          {executionResult && (
            <article className="message message--assistant">
              <div className="assistant-avatar">A</div>
              <div className="message__content">
                <p>{friendlyExecutionMessage(executionResult)}</p>
                <div
                  className={`result-card result-card--${executionResult.status}`}
                >
                  <div className="result-card__title">
                    <span aria-hidden="true">
                      {executionResult.status === "completed" ? "✓" : "!"}
                    </span>
                    {readableStatus(executionResult.status)}
                  </div>
                  <ul>
                    {executionResult.results.map((action, index) => (
                      <li key={action.action_id}>
                        <span>Step {index + 1}</span>
                        <strong>{readableStatus(action.status)}</strong>
                        {action.error && <p>{action.error}</p>}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </article>
          )}
        </section>

        <div className="composer-wrap">
          <form className="composer" onSubmit={submitText}>
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder="Ask Aura to do something on your desktop…"
              rows={1}
              disabled={appBusy}
              aria-label="Task request"
            />
            <div className="composer__actions">
              <span className={`capture-status capture-status--${status}`}>
                {CAPTURE_LABEL[status]}
              </span>
              <button
                className={`voice-button ${recording ? "voice-button--recording" : ""}`}
                type="button"
                onPointerDown={(event) => {
                  event.preventDefault();
                  void start();
                }}
                onPointerUp={stop}
                onPointerLeave={() => {
                  if (recording) stop();
                }}
                disabled={appBusy && !recording}
                aria-label="Hold to speak"
                title="Hold to speak"
              >
                <span className="mic-glyph" aria-hidden="true" />
              </button>
              <button
                className="send-button"
                type="submit"
                disabled={!draft.trim() || appBusy}
                aria-label="Send request"
              >
                ↑
              </button>
            </div>
          </form>
          {error && <p className="composer-error">{error}</p>}
          <p className="composer-note">
            Aura can make mistakes. Review plans before execution.
          </p>
        </div>
      </main>
    </div>
  );
}
