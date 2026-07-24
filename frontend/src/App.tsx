import { Fragment, FormEvent, useCallback, useEffect, useState } from "react";
import {
  createPlan,
  controlPlan,
  executePlan,
  getTask,
  listTasks,
  type PlanExecutionResponse,
  type PlanningResponse,
  type StepDetail,
  type TaskStatus,
  type TaskSummary,
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

function ErrorNotice({ details }: { details?: string | null }) {
  return (
    <div className="error-state" role="alert">
      <span className="error-state__icon" aria-hidden="true">!</span>
      <div>
        <strong>Sorry, something went wrong</strong>
        {details && <p>{details}</p>}
      </div>
    </div>
  );
}

function evidenceText(
  evidence: Record<string, unknown>,
  key: "content" | "summary",
): string | null {
  const value = evidence[key];
  return typeof value === "string" && value.trim() ? value : null;
}

/**
 * The data a step actually produced, collapsed by default.
 *
 * A run used to show only a status, which hid the whole point of the workflow:
 * a value came out of one file and went into another. This makes that chain
 * readable while keeping the plan scannable.
 *
 * Everything here arrives already clamped and redacted by the server. The
 * excerpt is rendered as a quotation because it is content read from a file,
 * not something the agent said.
 */
function StepDetailView({ detail }: { detail: StepDetail }) {
  // Normalised rather than destructured straight through, so a step whose detail
  // the server could not build degrades to nothing instead of throwing.
  const summary = detail.summary ?? "";
  const facts = detail.facts ?? [];
  const excerpt = detail.excerpt ?? null;
  const comparison = detail.comparison ?? null;
  const note = detail.note ?? null;
  const hasBody = facts.length > 0 || excerpt !== null || comparison !== null;
  if (!hasBody && !summary && !note) return null;

  return (
    <div className="step-detail">
      {summary && <p className="step-detail__summary">{summary}</p>}
      {note && (
        <p className="step-detail__note">
          <span aria-hidden="true">i</span>
          {note}
        </p>
      )}
      {hasBody && (
        <details className="step-detail__more">
          <summary>What this step produced</summary>
          {facts.length > 0 && (
            <dl className="step-detail__facts">
              {facts.map((fact) => (
                <Fragment key={`${fact.label}-${fact.value}`}>
                  <dt>{fact.label}</dt>
                  <dd>{fact.value}</dd>
                </Fragment>
              ))}
            </dl>
          )}
          {comparison && (
            <div className="step-detail__check">
              <span>
                Checked by reopening the file
                {comparison.method ? ` (${comparison.method})` : ""}
              </span>
              <dl>
                {comparison.expected != null && (
                  <>
                    <dt>Expected</dt>
                    <dd>{comparison.expected}</dd>
                  </>
                )}
                {comparison.observed != null && (
                  <>
                    <dt>Found on disk</dt>
                    <dd>{comparison.observed}</dd>
                  </>
                )}
              </dl>
            </div>
          )}
          {excerpt && (
            <figure className="step-detail__excerpt">
              <figcaption>
                {excerpt.label}
                {excerpt.untrusted && (
                  <em> — content from the file, not the assistant</em>
                )}
              </figcaption>
              <blockquote>{excerpt.body}</blockquote>
              {excerpt.truncated && (
                <small>Shortened for display — this is not the whole file.</small>
              )}
            </figure>
          )}
        </details>
      )}
    </div>
  );
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
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null);
  const [recentTasks, setRecentTasks] = useState<TaskSummary[]>([]);
  const [taskEvents, setTaskEvents] = useState<
    { event_type: string; message: string; created_at: string }[]
  >([]);
  const {
    status,
    result,
    error,
    liveTranscript,
    liveSupported,
    start,
    stop,
  } = useVoiceCapture();

  const recording = status === "recording";
  const captureBusy = status === "requesting" || status === "transcribing";
  const appBusy = captureBusy || planning || executing;

  useEffect(() => {
    fetch("/api/v1/voice/health")
      .then((response) => (response.ok ? response.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const refreshTasks = useCallback(() => {
    void listTasks().then(setRecentTasks).catch(() => undefined);
  }, []);

  useEffect(refreshTasks, [refreshTasks]);

  const requestPlan = useCallback(async (request: TaskRequest) => {
    setActiveRequest(request);
    setPlanning(true);
    setPlanningError(null);
    setPlanningResult(null);
    setExecutionResult(null);
    setExecutionError(null);
    setTaskEvents([]);

    try {
      const response = await createPlan(request);
      setPlanningResult(response);
      setTaskStatus(response.plan ? "planned" : null);
      refreshTasks();
    } catch (reason: unknown) {
      setPlanningError(
        reason instanceof Error
          ? reason.message
          : "I couldn't prepare that plan.",
      );
    } finally {
      setPlanning(false);
    }
  }, [refreshTasks]);

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
    const approvedActionHashes = Object.fromEntries(
      plan.actions
        .filter(
          (action) =>
            action.requires_confirmation && action.confirmation_hash != null,
        )
        .map((action) => [action.action_id, action.confirmation_hash as string]),
    );

    setExecuting(true);
    setTaskStatus("running");
    setExecutionError(null);
    try {
      const response = await executePlan(
        plan.plan_id,
        approvedActionIds,
        approvedActionHashes,
      );
      setExecutionResult(response);
      setTaskStatus(response.status);
      setTaskEvents((await getTask(plan.plan_id)).events);
      refreshTasks();
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

  const rejectConfirmation = async () => {
    const plan = planningResult?.plan;
    if (!plan || executing) return;
    setExecutionError(null);
    try {
      const response = await executePlan(plan.plan_id, [], {});
      setExecutionResult(response);
      setTaskEvents((await getTask(plan.plan_id)).events);
    } catch (reason: unknown) {
      setExecutionError(
        reason instanceof Error ? reason.message : "Could not reject the action",
      );
    }
  };

  const changeTaskState = async (intent: "pause" | "resume" | "cancel") => {
    const planId = planningResult?.plan?.plan_id;
    if (!planId) return;
    try {
      const task = await controlPlan(planId, intent);
      setTaskStatus(task.status);
      setTaskEvents(task.events);
      refreshTasks();
    } catch (reason: unknown) {
      setExecutionError(
        reason instanceof Error ? reason.message : `Could not ${intent} task`,
      );
    }
  };

  const openRecentTask = async (task: TaskSummary) => {
    if (executing) return;
    try {
      const detail = await getTask(task.plan_id);
      setActiveRequest({
        request_id: detail.request_id,
        text: detail.request_text,
        source: "text",
        confidence: null,
        received_at: detail.created_at,
      });
      setPlanningResult({
        request_id: detail.request_id,
        control_intent: null,
        plan: detail.plan,
        refusal: null,
      });
      setExecutionResult(
        detail.results.length
          ? { plan_id: detail.plan_id, status: detail.status, results: detail.results }
          : null,
      );
      setTaskStatus(detail.status);
      setTaskEvents(detail.events);
      setPlanningError(null);
      setExecutionError(null);
    } catch (reason: unknown) {
      setExecutionError(reason instanceof Error ? reason.message : "Could not load task");
    }
  };

  const resetConversation = () => {
    setActiveRequest(null);
    setPlanningResult(null);
    setPlanningError(null);
    setExecutionResult(null);
    setExecutionError(null);
    setTaskStatus(null);
    setTaskEvents([]);
    setDraft("");
  };

  const beginVoiceRequest = () => {
    resetConversation();
    void start();
  };

  const lowConfidence =
    activeRequest?.confidence != null &&
    activeRequest.confidence < LOW_CONFIDENCE;
  const hasConversation =
    activeRequest ||
    recording ||
    status === "transcribing" ||
    planning ||
    planningResult ||
    planningError;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand__mark">V</span>
          <span>Voice desk</span>
        </div>

        <button className="new-task" type="button" onClick={resetConversation}>
          <span aria-hidden="true">＋</span>
          New task
        </button>

        <div className="sidebar__section">
          <p className="sidebar__label">Recent tasks</p>
          <div className="task-history">
            {recentTasks.length === 0 && (
              <p className="task-history__empty">Your completed tasks will appear here.</p>
            )}
            {recentTasks.map((task) => (
              <button
                className="task-history__item"
                type="button"
                key={task.plan_id}
                onClick={() => void openRecentTask(task)}
                disabled={executing}
              >
                <span>{task.request_text}</span>
                <small className={`task-status task-status--${task.status}`}>
                  {readableStatus(task.status)}
                </small>
              </button>
            ))}
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
            <p className="eyebrow">VOICE-CONTROLLED AGENT</p>
            <h1>Voice desk</h1>
          </div>
          <div className="privacy-pill">
            <span aria-hidden="true">◆</span>
            Local-first workspace
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

          {(recording || status === "transcribing") && (
            <article className="message message--user message--live">
              <div className="message__avatar">You</div>
              <div className="message__content">
                <p>
                  {liveTranscript ||
                    (liveSupported
                      ? "Listening…"
                      : "Listening — your words will appear when you release.")}
                  {recording && <span className="live-caret" aria-hidden="true" />}
                </p>
                <span>
                  {recording ? "Live voice transcript" : "Finishing transcript…"}
                </span>
              </div>
            </article>
          )}

          {planning && (
            <article className="message message--assistant">
              <div className="assistant-avatar">V</div>
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
              <div className="assistant-avatar">V</div>
              <div className="message__content">
                <ErrorNotice details={planningError} />
              </div>
            </article>
          )}

          {planningResult?.refusal && (
            <article className="message message--assistant">
              <div className="assistant-avatar">V</div>
              <div className="message__content">
                <p>{planningResult.refusal}</p>
                <small>
                  I stopped here on purpose rather than guessing at a plan that
                  would fail.
                </small>
              </div>
            </article>
          )}

          {planningResult?.control_intent && (
            <article className="message message--assistant">
              <div className="assistant-avatar">V</div>
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
              <div className="assistant-avatar">V</div>
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

                  {(!executionResult || executionResult.status === "blocked") && (
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
                      {planningResult.plan.actions.some(
                        (action) => action.requires_confirmation,
                      ) && (
                        <button
                          type="button"
                          onClick={() => void rejectConfirmation()}
                          disabled={executing}
                        >
                          Reject consequential step
                        </button>
                      )}
                      {executing && (
                        <div className="task-controls" aria-label="Task controls">
                          {taskStatus === "paused" ? (
                            <button type="button" onClick={() => void changeTaskState("resume")}>
                              Resume
                            </button>
                          ) : (
                            <button type="button" onClick={() => void changeTaskState("pause")}>
                              Pause
                            </button>
                          )}
                          <button
                            className="task-controls__cancel"
                            type="button"
                            onClick={() => void changeTaskState("cancel")}
                          >
                            Cancel
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </article>
          )}

          {executionError && (
            <article className="message message--assistant">
              <div className="assistant-avatar">V</div>
              <div className="message__content">
                <ErrorNotice details={executionError} />
              </div>
            </article>
          )}

          {executionResult && (
            <article className="message message--assistant">
              <div className="assistant-avatar">V</div>
              <div className="message__content">
                {executionResult.status === "failed" ||
                executionResult.status === "blocked" ? (
                  <ErrorNotice details={friendlyExecutionMessage(executionResult)} />
                ) : (
                  <p>{friendlyExecutionMessage(executionResult)}</p>
                )}
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
                        {action.verification && (
                          <div
                            className={`verification verification--${
                              action.verification.passed === true
                                ? "passed"
                                : action.verification.passed === false
                                  ? "failed"
                                  : "unknown"
                            }`}
                          >
                            <span aria-hidden="true">
                              {action.verification.passed === true
                                ? "✓"
                                : action.verification.passed === false
                                  ? "!"
                                  : "○"}
                            </span>
                            {action.verification.message}
                          </div>
                        )}
                        {action.detail && (
                          <StepDetailView detail={action.detail} />
                        )}
                        {evidenceText(action.evidence, "summary") && (
                          <div className="evidence-panel evidence-panel--summary">
                            <span>Email summary</span>
                            <p>{evidenceText(action.evidence, "summary")}</p>
                          </div>
                        )}
                        {evidenceText(action.evidence, "content") && (
                          <div className="evidence-panel">
                            <span>File content</span>
                            <pre>{evidenceText(action.evidence, "content")}</pre>
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </article>
          )}

          {taskEvents.length > 0 && (
            <article className="message message--assistant">
              <div className="assistant-avatar">V</div>
              <div className="message__content">
                <details className="activity-log">
                  <summary>Task activity</summary>
                  <ol>
                    {taskEvents.map((event, index) => (
                      <li key={`${event.created_at}-${index}`}>
                        <span>{event.message}</span>
                        <time dateTime={event.created_at}>
                          {new Date(event.created_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </time>
                      </li>
                    ))}
                  </ol>
                </details>
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
              placeholder="Ask Voice desk to do something on your desktop…"
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
                  beginVoiceRequest();
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
          {error && <ErrorNotice details={error} />}
          <p className="composer-note">
            Voice desk can make mistakes. Review plans before execution.
          </p>
        </div>
      </main>
    </div>
  );
}
