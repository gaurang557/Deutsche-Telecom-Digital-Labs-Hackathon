import { useCallback, useRef, useState } from "react";
import { transcribeAudio, type TaskRequest } from "./api";

export type CaptureStatus =
  | "idle"
  | "requesting"
  | "recording"
  | "transcribing"
  | "error";

/**
 * Push-to-talk microphone capture.
 *
 * `start` opens the mic and begins recording; `stop` ends it, uploads the clip
 * to the backend, and exposes the resulting transcript. State flows
 * idle -> requesting -> recording -> transcribing -> idle (or error).
 */
export function useVoiceCapture() {
  const [status, setStatus] = useState<CaptureStatus>("idle");
  const [result, setResult] = useState<TaskRequest | null>(null);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const start = useCallback(async () => {
    if (status === "recording" || status === "requesting") return;
    setError(null);
    setResult(null);
    setStatus("requesting");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        if (blob.size === 0) {
          setStatus("idle");
          return;
        }
        setStatus("transcribing");
        try {
          setResult(await transcribeAudio(blob));
          setStatus("idle");
        } catch (err) {
          setError(String(err));
          setStatus("error");
        }
      };

      recorder.start();
      recorderRef.current = recorder;
      setStatus("recording");
    } catch (err) {
      setError(String(err));
      setStatus("error");
    }
  }, [status]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    }
  }, []);

  return { status, result, error, start, stop };
}
