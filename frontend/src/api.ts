export interface TaskRequest {
  request_id: string;
  text: string;
  source: string;
  confidence: number | null;
  received_at: string;
}

export interface PlannedAction {
  action_id: string;
  sequence: number;
  type: string;
  target: string;
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
    throw new Error(`Transcription failed (HTTP ${response.status})`);
  }
  return (await response.json()) as TaskRequest;
}

/** Send a transcribed request to the validated Ollama planning boundary. */
export async function createPlan(
  request: TaskRequest,
): Promise<PlanningResponse> {
  const response = await fetch("/api/v1/plans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(`Planning failed (HTTP ${response.status})`);
  }
  return (await response.json()) as PlanningResponse;
}
