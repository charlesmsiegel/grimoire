import { useEffect, useState } from "react";

interface HealthResponse {
  status: string;
  version: string;
  data_root: string;
}

export function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<HealthResponse>;
      })
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main>
      <h1>Grimoire</h1>
      {error && <p>Backend unreachable: {error}</p>}
      {health && (
        <dl>
          <dt>Status</dt>
          <dd>{health.status}</dd>
          <dt>Version</dt>
          <dd>{health.version}</dd>
          <dt>Data root</dt>
          <dd>
            <code>{health.data_root}</code>
          </dd>
        </dl>
      )}
    </main>
  );
}
