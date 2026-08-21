import { NavLink } from "react-router-dom";
import type { ProviderHealth } from "../api/client";
import { useFocus } from "./focus";
import { usePalette } from "./palette";
import { useShellStatus } from "./ShellStatus";

/** The dot's three states, and the words that go with them.
 *
 *  `ready` is a statement about the *stored settings* — a key string is
 *  present, a base URL is set — and until #146 it was the only thing the header
 *  had, so a revoked key drew the same green dot as a working one. `health` is
 *  what the provider last actually did: a real turn's outcome, or an explicit
 *  Test connection.
 *
 *  A configured-but-unproven connection stays green rather than going amber.
 *  It is the state every app start begins in, and a warning shown to everyone
 *  every morning is a warning nobody reads by lunchtime; the tooltip says "not
 *  checked" for the reader who looks.
 */
function verdict(connection: string, ready: boolean, health: ProviderHealth | null) {
  if (!connection) return { tone: "off", words: "No connection" };
  if (!ready) return { tone: "off", words: `${connection}, not ready` };
  if (health?.state === "error") {
    return { tone: "bad", words: `${connection}, failing: ${health.detail || health.kind}` };
  }
  if (health?.state === "ok") return { tone: "ok", words: `${connection}, connected` };
  return { tone: "ok", words: `${connection}, not checked yet` };
}

/** The 52px strip across the top, and the only chrome that never moves.
 *
 *  Left: the mark and the wordmark, both home. Middle: the ⌘K pill, which is
 *  the app's whole navigation surface — it names where you are and opens the
 *  palette that takes you anywhere else. Right: what the next turn will cost
 *  and cost against — the model, how full the context budget is, whether the
 *  connection is usable and whether it last worked — and CONFIG.
 *
 *  There is no nav sidebar and no scene rail. That is not an omission. */
export default function AppHeader(
  { model, connection, ready, health }: {
    model: string; connection: string; ready: boolean; health: ProviderHealth | null;
  },
) {
  const { setOpen } = usePalette();
  const { setFocus } = useFocus();
  const { context, usage, sceneModel } = useShellStatus();
  // The open campaign's scene model wins over the global one: with per-task
  // routing (#142) the active connection is not necessarily what writes this
  // campaign's prose, and the header exists to name what the next turn costs.
  const shown = sceneModel ?? model;

  const where = context
    ? (context.scene ? `${context.campaign} / ${context.scene}` : context.campaign)
    : "go anywhere";
  const status = verdict(connection, ready, health);

  return (
    <header className="app-header">
      <NavLink to="/" className="brand">
        <img src="/grimoire-128.png" alt="" width={30} height={30} />
        <span>GRIMOIRE</span>
      </NavLink>

      <button type="button" className="kbar" onClick={() => setOpen(true)}
              aria-haspopup="dialog" aria-label={`Go anywhere. Currently: ${where}`}>
        <span className="kbar-key" aria-hidden>⌘K</span>
        <span className="kbar-where">{where}</span>
        <span className="kbar-caret" aria-hidden>▾</span>
      </button>

      <span className="header-spacer" />

      <div className="header-status">
        {shown && <span className="header-model">{shown.toUpperCase()}</span>}
        {usage !== null && (
          <span className="header-ctx" title="How full the last prompt left the context budget">
            CTX {Math.round(usage)}%
          </span>
        )}
        {/* The dot is the whole connection widget. Its title carries the name
            and the verdict, because a coloured dot that cannot be hovered —
            on a phone — must still not be the only place a broken connection
            is reported; the Connections page says it in words, beside the
            key it is about. */}
        <span className={"conn-dot " + status.tone} title={status.words}>
          <span aria-hidden>●</span>
          <span className="sr-only">{status.words}</span>
        </span>
      </div>

      {/* The way out of the chrome, and it lives in the chrome: on a phone
          this strip plus the scene bar plus the scene head is most of a
          screenful, and a transcript is the one thing in the app that is worth
          the whole viewport. Its counterpart -- the pill that brings the bars
          back -- is `FocusRestore`, which the shell renders in its place. */}
      <button type="button" className="header-focus" onClick={() => setFocus(true)}
              title="Hide the toolbars and read the scene" aria-label="Enter focus mode">
        FOCUS
      </button>

      <NavLink to="/config"
               className={({ isActive }) => "config-link" + (isActive ? " active" : "")}>
        CONFIG
      </NavLink>
    </header>
  );
}
