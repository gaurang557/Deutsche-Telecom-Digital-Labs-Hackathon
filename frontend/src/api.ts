export interface TaskRequest {
  request_id: string;
  text: string;
  source: "speech" | "text";
  confidence: number | null;
  received_at: string;
}

export interface PlanRequest {
  text: string;
  source: "speech" | "text";
  request_id?: string;
  confidence?: number | null;
  received_at?: string;
}

export interface PlannedAction {
  action_id: string;
  sequence: number;
  type: string;
  target: string;
  description: string;
  risk: "low" | "medium" | "high";
  requires_confirmation: boolean;
  confirmation_hash: string | null;
}

export interface PlanningResponse {
  request_id: string;
  control_intent: "pause" | "resume" | "cancel" | "correct" | null;
  plan: {
    plan_id: string;
    summary: string;
    actions: PlannedAction[];
  } | null;
  /** Present when no available action can satisfy the request. */
  refusal: string | null;
}

export interface ActionResult {
  action_id: string;
  status: "succeeded" | "failed" | "blocked" | "cancelled";
  evidence: Record<string, unknown>;
  error: string | null;
  verification: {
    passed: boolean | null;
    message: string;
    evidence: Record<string, unknown>;
  } | null;
}

export type TaskStatus =
  | "planned"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "blocked"
  | "cancelled";

export interface PlanExecutionResponse {
  plan_id: string;
  status: TaskStatus;
  results: ActionResult[];
}

export interface TaskSummary {
  plan_id: string;
  request_id: string;
  request_text: string;
  summary: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
}

export interface TaskDetail extends TaskSummary {
  plan: NonNullable<PlanningResponse["plan"]>;
  results: ActionResult[];
  events: {
    event_type: string;
    message: string;
    created_at: string;
  }[];
}

/** Upload a recorded audio clip and get back the transcribed TaskRequest. */
export async function transcribeAudio(blob: Blob): Promise<TaskRequest> {
  const form = new FormData();
  const extension = blob.type.includes("webm") ? "webm" : "audio";
  form.append("file", blob, `clip.${extension}`);

  const response = await fetch("/api/v1/voice/transcribe", {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      body?.detail ?? `Transcription failed (HTTP ${response.status})`,
    );
  }
  return (await response.json()) as TaskRequest;
}

/** Send a transcribed request to the validated Ollama planning boundary. */
export async function createPlan(
  request: PlanRequest,
): Promise<PlanningResponse> {
  const response = await fetch("/api/v1/plans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `Planning failed (HTTP ${response.status})`);
  }
  return (await response.json()) as PlanningResponse;
}

export async function executePlan(
  planId: string,
  approvedActionIds: string[],
  approvedActionHashes: Record<string, string> = {},
): Promise<PlanExecutionResponse> {
  const response = await fetch(`/api/v1/plans/${planId}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      approved_action_ids: approvedActionIds,
      approved_action_hashes: approvedActionHashes,
    }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `Execution failed (HTTP ${response.status})`);
  }
  return (await response.json()) as PlanExecutionResponse;
}

export async function listTasks(): Promise<TaskSummary[]> {
  const response = await fetch("/api/v1/tasks");
  if (!response.ok) throw new Error("Could not load recent tasks");
  return (await response.json()) as TaskSummary[];
}

export async function getTask(planId: string): Promise<TaskDetail> {
  const response = await fetch(`/api/v1/tasks/${planId}`);
  if (!response.ok) throw new Error("Could not load task");
  return (await response.json()) as TaskDetail;
}

export async function controlPlan(
  planId: string,
  intent: "pause" | "resume" | "cancel",
): Promise<TaskDetail> {
  const response = await fetch(`/api/v1/plans/${planId}/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ intent }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `Could not ${intent} task`);
  }
  return (await response.json()) as TaskDetail;
}
