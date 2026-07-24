export interface TaskRequest {
  request_id: string;
  text: string;
  source: string;
  confidence: number | null;
  received_at: string;
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
