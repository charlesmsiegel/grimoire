import { Link, useInRouterContext } from "react-router-dom";

/** Why a generating control is off while the app cannot generate: no Primary
 *  model that can send. Settings no longer sets anything up -- it links out --
 *  so this names the page where the choice is made, and links there wherever
 *  a router is mounted (a bare render of a form in a test has none). One
 *  sentence for every such control, so they cannot drift apart again. */
export function NeedsModel({ action = "generate" }: { action?: string }) {
  const routed = useInRouterContext();
  return (
    <div className="field-hint">
      Choose a provider and a Primary model on the{" "}
      {routed ? <Link to="/models">Models page</Link> : "Models page"} to {action}.
    </div>
  );
}
