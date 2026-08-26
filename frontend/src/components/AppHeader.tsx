import { NavLink, useLocation } from "react-router-dom";
import type { ProviderHealth } from "../api/client";
import { railless, titleFor } from "../shell/rail";
import { useThemeSetting } from "../theme/useThemeSetting";
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

/** The 52px strip across the top.
 *
 *  Left: the mark and the wordmark, both home, and — below `RAIL_PX` — the
 *  control that opens the nav rail as a drawer. Middle: the ⌘K pill, which
 *  names where you are and opens the palette that takes you anywhere else.
 *  Right: the scene pill (only where there is a scene), what the next turn will
 *  cost against, the look, and the way into focus mode.
 *
 *  The rail is the app's navigation now, and Configuration is a row on it
 *  rather than a link here. The pill is still how you go *anywhere* — the rail
 *  lists the places worth a permanent row, and says what is waiting at each. */
export default function AppHeader(
  { model, connection, ready, health, onOpenRail, railDrawer }: {
    model: string; connection: string; ready: boolean; health: ProviderHealth | null;
    /** Opens the rail as a drawer. Rendered only below `RAIL_PX`. */
    onOpenRail: () => void;
    /** True while the rail is a drawer rather than docked. */
    railDrawer: boolean;
  },
) {
  const { setOpen } = usePalette();
  const { setFocus } = useFocus();
  const theme = useThemeSetting();
  const { pathname } = useLocation();
  const { context, usage, sceneModel, sceneReady } = useShellStatus();
  // The open campaign's scene model wins over the global one: with per-task
  // routing (#142) the active connection is not necessarily what writes this
  // campaign's prose, and the header exists to name what the next turn costs.
  const shown = sceneModel ?? model;
  // And the dot goes with it. A campaign whose scene turns are routed at a
  // keyless connection 409s every send; a green dot beside that is the chrome
  // reporting on a connection this page is not using.
  const live = sceneReady ?? ready;

  // A page that publishes context wins over the route table: the router knows
  // the cid but not the campaign's name, and only the page can answer that.
  // Everything else is named centrally rather than by twenty publish hooks —
  // the table's last entry matches everything, so no route reaches the pill
  // nameless.
  const where = context
    ? (context.scene ? `${context.campaign} / ${context.scene}` : context.campaign)
    : titleFor(pathname);
  // The context budget belongs to a scene, so it is shown beside one and
  // nowhere else. A percentage on a page with no scene is a claim about a
  // prompt you are not composing — the argument `ShellStatus` already makes
  // about a campaign name outliving its page.
  const inScene = !!context?.scene;
  // `live`, not `ready`: the dot answers for the connection this page's next
  // turn will actually use (#142), and `verdict` decides its colour from
  // exactly that plus what the provider last did (#146).
  const status = verdict(connection, live, health);

  return (
    <header className="app-header">
      {/* Below RAIL_PX the rail is a drawer, and this is its only opener. Not
          rendered where the rail itself is not (the two wizards), because a
          control that opens nothing is worse than no control. */}
      {railDrawer && !railless(pathname) && (
        <button type="button" className="header-rail" onClick={onOpenRail}
                aria-haspopup="dialog" aria-label="Open navigation">
          <span aria-hidden>☰</span>
        </button>
      )}
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

      {/* The scene pill. The design pairs the context percentage with the
          scene's spend; the money half is not built here — the rail and this
          pill both stay out of the ledger until the costs slice gives them a
          maintained aggregate to read. */}
      {inScene && usage !== null && (
        <span className="scene-pill" title="How full the last prompt left the context budget">
          CTX {Math.round(usage)}%
        </span>
      )}

      <div className="header-status">
        {shown && <span className="header-model">{shown.toUpperCase()}</span>}
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
      {/* Persisted on click, through the hook Configuration's picker and the
          first-run wizard's share. A look that only lasted the session would
          read as the app forgetting; a look this control wrote into a deferred
          draft could be overwritten by an unrelated Save elsewhere. */}
      <button type="button" className="header-theme" disabled={theme.busy}
              onClick={() => { void theme.pick(theme.mode === "dark" ? "light" : "dark"); }}
              aria-label={`Theme: ${theme.mode}. Switch.`}>
        {theme.mode.toUpperCase()}
      </button>

      <button type="button" className="header-focus" onClick={() => setFocus(true)}
              title="Hide the toolbars and read the scene" aria-label="Enter focus mode">
        FOCUS
      </button>
    </header>
  );
}
