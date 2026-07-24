import { useEffect, useState } from "react";

interface VoiceHealth {
  status: string;
  model: string;
  model_loaded: boolean;
}

type Backend =
  | { state: "loading" }
  | { state: "ok"; health: VoiceHealth }
  | { state: "error"; message: string };

export default function App() {
  const [backend, setBackend] = useState<Backend>({ state: "loading" });

  useEffect(() => {
    fetch("/api/v1/voice/health")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<VoiceHealth>;
      })
      .then((health) => setBackend({ state: "ok", health }))
      .catch((err) => setBackend({ state: "error", message: String(err) }));
  }, []);

  return (
    <main className="app">
      <h1 className="title">Voice Agent</h1>
      <p className="subtitle">Local voice-controlled desktop agent</p>

      <div className="status-card">
        {backend.state === "loading" && (
          <span className="status status--pending">Connecting to backend…</span>
        )}
        {backend.state === "error" && (
          <span className="status status--error">
            Backend unreachable — {backend.message}
          </span>
        )}
        {backend.state === "ok" && (
          <span className="status status--ok">
            Backend {backend.health.status} · model {backend.health.model} ·{" "}
            {backend.health.model_loaded ? "loaded" : "not loaded"}
          </span>
        )}
      </div>
    </main>
  );
}
