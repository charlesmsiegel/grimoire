import { Link, useLocation } from "react-router-dom";
import { errMsg, isOffline } from "./errMsg";

/** What a `network` failure says instead of its raw socket error (#210).
 *
 *  The library half of this app has never needed the network — worlds,
 *  campaigns, characters and scenes are files under `GRIMOIRE_HOME` — and
 *  since #141 the model half need not either: an OpenAI-compatible connection
 *  pointed at Ollama, LM Studio, llama.cpp-server or vLLM answers from this
 *  machine. So the useful thing to say is not "you are offline" but where the
 *  recovery is, which is why this links to Connections rather than just
 *  reporting.
 *
 *  The provider's own message stays on screen. `network` is broader than "no
 *  internet" — a local endpoint that is simply not running raises exactly the
 *  same kind — and a reader debugging *that* needs the refused address, not a
 *  paragraph about being offline. The advice below is worded to hold in both
 *  cases: Connections is where a wrong base URL gets fixed too. */
function OfflineNote({ detail }: { detail: string }) {
  // The link is dropped on the page it points at -- the Connections editor
  // raises this note too, when a model-catalog fetch cannot reach the
  // provider, and a link to the page you are reading is noise. Read from the
  // router rather than taken as a prop so no caller can forget it.
  const here = useLocation().pathname === "/connections";
  return (
    <span>
      <strong>Couldn’t reach the model provider.</strong> {detail}
      {" — "}your library is on this machine, so everything but the model still
      works. A local model connection (Ollama, LM Studio, llama.cpp) keeps play
      going with no network at all.{" "}
      {!here && <Link to="/connections">Connections →</Link>}
    </span>
  );
}

/** The whole of what a failed call should say: the offline note when the
 *  failure is `network`, the plain detail otherwise.
 *
 *  A fragment rather than a banner, because the banner around it differs per
 *  surface — the scene view's carries a Retry button and lives in a flex row,
 *  the panels' is a bare `.banner` — and only the message inside it is shared. */
export function ErrorNote({ err }: { err: unknown }) {
  return isOffline(err) ? <OfflineNote detail={errMsg(err)} /> : <>{errMsg(err)}</>;
}
