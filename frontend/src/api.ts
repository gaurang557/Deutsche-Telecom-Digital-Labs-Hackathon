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
}

export interface PlanningResponse {
  request_id: string;
  control_intent: "pause" | "resume" | "cancel" | "correct" | null;
  plan: {
    plan_id: string;
    summary: string;
    actions: PlannedAction[];
  } | null;
}

export interface ActionResult {
  action_id: string;
  status: "succeeded" | "failed" | "blocked" | "cancelled";
  evidence: Record<string, unknown>;
  error: string | null;
}

export interface PlanExecutionResponse {
  plan_id: string;
  status: "completed" | "failed" | "blocked" | "cancelled";
  results: ActionResult[];
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
): Promise<PlanExecutionResponse> {
  const response = await fetch(`/api/v1/plans/${planId}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved_action_ids: approvedActionIds }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `Execution failed (HTTP ${response.status})`);
  }
  return (await response.json()) as PlanExecutionResponse;
}
