import { useShellStatus, type ShellContext } from "./ShellStatus";

export type StatusBarProps = {
  /** Display name of the active LLM connection; "" when none is active. */
  connection: string;
  /** Whether that connection is usable (has its key / base URL). */
  ready: boolean;
  /** The model every scene will run on — the active connection's, since
   *  there is no per-campaign override to scope it to. "" when unchosen. */
  model: string;
  context: ShellContext;
  /** The three widgets the feature list asks for that have no data source in
   *  the app yet (#126 tiered token budget, #174 generation queue, #59 voice
   *  drift). They are props, not TODOs, so those follow-ups fill a slot
   *  instead of reshaping the bar — and until then the bar shows a dash and
   *  says why, rather than a fabricated zero. */
  budget?: string;
  queue?: string;
  drift?: string;
};

const RESERVED_NOTE = "Not available yet";

function Cell({ id, label, value, title }: {
  id: string; label: string; value?: string; title?: string;
}) {
  const shown = value?.trim() ? value : "—";
  return (
    <span className={"status-cell" + (shown === "—" ? " empty" : "")}
          data-testid={`status-${id}`} title={title}>
      <span className="status-label">{label}</span>
      <span className="status-value">{shown}</span>
    </span>
  );
}

/** The always-visible bottom strip. Presentational on purpose: every value
 *  arrives as a prop, so it renders identically in a test and under a
 *  half-loaded app, and the unimplemented widgets can be filled in later
 *  without touching this file's layout. */
export default function StatusBar({
  connection, ready, model, context, budget, queue, drift,
}: StatusBarProps) {
  return (
    <footer className="statusbar" aria-label="Status">
      <span className="status-cell status-conn" data-testid="status-connection">
        <span className="dot" aria-hidden>●</span>
        <span className="status-value">
          {connection ? `${connection} · ${ready ? "CONNECTED" : "NOT READY"}` : "NO CONNECTION"}
        </span>
      </span>
      <Cell id="model" label="Model" value={model} title="Model the active connection will run" />
      {context && (
        <span className="status-cell status-where" data-testid="status-context">
          <span className="status-value">
            {context.scene ? `${context.campaign} ▸ ${context.scene}` : context.campaign}
          </span>
        </span>
      )}
      <span className="status-spacer" />
      <Cell id="tokens" label="Tokens" value={budget} title={`${RESERVED_NOTE} — no token budget exists (#126)`} />
      <Cell id="queue" label="Queue" value={queue} title={`${RESERVED_NOTE} — no request queue exists (#174)`} />
      <Cell id="drift" label="Drift" value={drift} title={`${RESERVED_NOTE} — no drift metric exists (#59)`} />
    </footer>
  );
}

/** The shell's instance: identical to `StatusBar` but reads the page-published
 *  location out of context, so `App` can render it as a sibling of `Routes`. */
export function GlobalStatusBar(props: Omit<StatusBarProps, "context">) {
  const { context } = useShellStatus();
  return <StatusBar {...props} context={context} />;
}
