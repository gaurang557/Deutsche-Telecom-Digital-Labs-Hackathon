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
  const [liveTranscript, setLiveTranscript] = useState("");

  const recorderRef = useRef<MediaRecorder | null>(null);
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const liveFinalRef = useRef("");
  const Recognition =
    window.SpeechRecognition ?? window.webkitSpeechRecognition;
  const liveSupported = Boolean(Recognition);

  const start = useCallback(async () => {
    if (status === "recording" || status === "requesting") return;
    setError(null);
    setResult(null);
    setLiveTranscript("");
    liveFinalRef.current = "";
    setStatus("requesting");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      if (Recognition) {
        const recognition = new Recognition();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = navigator.language || "en-US";
        recognition.onresult = (event) => {
          let interim = "";
          for (let index = event.resultIndex; index < event.results.length; index += 1) {
            const phrase = event.results[index][0]?.transcript ?? "";
            if (event.results[index].isFinal) {
              liveFinalRef.current = `${liveFinalRef.current} ${phrase}`.trim();
            } else {
              interim += phrase;
            }
          }
          setLiveTranscript(
            `${liveFinalRef.current} ${interim}`.trim(),
          );
        };
        recognition.onerror = () => {
          recognitionRef.current = null;
        };
        recognitionRef.current = recognition;
        try {
          recognition.start();
        } catch {
          recognitionRef.current = null;
        }
      }

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };

      recorder.onstop = async () => {
        recognitionRef.current?.stop();
        recognitionRef.current = null;
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
          const transcript = await transcribeAudio(blob);
          setLiveTranscript(transcript.text);
          setResult(transcript);
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
      recognitionRef.current?.stop();
      recorder.stop();
    }
  }, []);

  return {
    status,
    result,
    error,
    liveTranscript,
    liveSupported,
    start,
    stop,
  };
}
